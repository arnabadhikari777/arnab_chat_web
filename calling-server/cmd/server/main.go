// Goppo calling server: reliable WebRTC signaling + session management, with mandatory C, C++ and Rust components.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/goppo/calling-server/internal/auth"
	"github.com/goppo/calling-server/internal/config"
	"github.com/goppo/calling-server/internal/native"
	"github.com/goppo/calling-server/internal/session"
	"github.com/goppo/calling-server/internal/signaling"
	"github.com/joho/godotenv"
)

func main() {
	selftest := flag.Bool("selftest", false, "load and verify the native C/C++/Rust components, print their status and exit (used by the Docker build)")
	flag.Parse()
	if *selftest {
		err := native.Init()
		for _, c := range native.Components() {
			fmt.Printf("%-13s lang=%-4s loaded=%v verified=%v %s\n", c.Name, c.Language, c.Loaded, c.Verified, c.Version)
		}
		if err != nil {
			fmt.Println("SELFTEST FAILED:", err)
			os.Exit(1)
		}
		fmt.Println("SELFTEST OK: C, C++ and Rust components are linked and verified")
		return
	}
	_ = godotenv.Load()
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("configuration error: %v", err)
	}

	// Native components are mandatory. There is no silent fallback: a failed self-test stops the server.
	if err := native.Init(); err != nil {
		if cfg.RequireNative {
			log.Fatalf("FATAL native components not operational: %v", err)
		}
		log.Printf("WARNING native components not operational (REQUIRE_NATIVE=false): %v", err)
	}
	for _, c := range native.Components() {
		log.Printf("component %-13s lang=%-4s loaded=%v verified=%v %s", c.Name, c.Language, c.Loaded, c.Verified, c.Version)
	}

	scfg := session.DefaultConfig()
	scfg.RingTimeout, scfg.ReconnectGrace = cfg.RingTimeout, cfg.ReconnectGrace
	mgr := session.NewManager(scfg)
	val := auth.NewValidator(cfg.Secret, cfg.TokenMaxAge)
	srv := signaling.New(cfg, val, mgr)

	stopTick := make(chan struct{})
	go func() {
		t := time.NewTicker(time.Second)
		defer t.Stop()
		for {
			select {
			case <-t.C:
				mgr.Tick()
			case <-stopTick:
				return
			}
		}
	}()

	hs := &http.Server{
		Addr: ":" + cfg.Port, Handler: srv.Handler(),
		ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 120 * time.Second,
	}
	go func() {
		log.Printf("goppo calling server listening on :%s (origins: %v)", cfg.Port, cfg.AllowedOrigins)
		if err := hs.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Println("shutting down")
	srv.Drain()
	mgr.Shutdown()
	close(stopTick)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = hs.Shutdown(ctx)
}
