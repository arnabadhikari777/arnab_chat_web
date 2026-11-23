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

	"github.com/goppo/calling-server/internal/admin"
	"github.com/goppo/calling-server/internal/auth"
	"github.com/goppo/calling-server/internal/config"
	"github.com/goppo/calling-server/internal/native"
	"github.com/goppo/calling-server/internal/session"
	"github.com/goppo/calling-server/internal/signaling"
	"github.com/goppo/calling-server/internal/turn"
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

	// TURN provider manager (Metered + optional Turnix). Default active = Metered.
	configPath := cfg.TurnConfigPath
	if configPath == "" {
		// Best-effort local/ephemeral path; on Render free this will not survive restart.
		configPath = "/tmp/goppo-turn-config.json"
	}
	tm := turn.NewManager(
		cfg.STUNURLs, cfg.TURNURLs,
		cfg.TURNSharedSecret, cfg.TURNStaticUser, cfg.TURNStaticPass,
		cfg.TURNCredTTL,
		cfg.TurnixAPIToken,
		configPath,
	)
	// Bootstrap from env if no durable selection yet.
	if st := tm.Status(); st.ActiveProvider == turn.ProviderMetered && cfg.ActiveTurnProvider == turn.ProviderTurnix && cfg.TurnixAPIToken != "" {
		_ = tm.SetActive(turn.ProviderTurnix, false)
	}

	adm := admin.New(cfg.AdminPassword, cfg.Secret, tm)
	if adm.Enabled() {
		log.Printf("admin panel enabled at /admin (password from ADMIN_PASSWORD)")
	} else {
		log.Printf("admin panel disabled (set ADMIN_PASSWORD to enable)")
	}

	srv := signaling.New(cfg, val, mgr, tm, adm)

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
		log.Printf("goppo calling server listening on :%s (origins: %v, active TURN: %s)", cfg.Port, cfg.AllowedOrigins, tm.Active())
		if err := hs.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Printf("shutting down…")
	srv.Drain()
	close(stopTick)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	_ = hs.Shutdown(ctx)
}
