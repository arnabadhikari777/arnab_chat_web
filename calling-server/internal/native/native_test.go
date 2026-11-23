package native

import "testing"

const sdp = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 AA:BB\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

func TestInitVerifiesAllThreeComponents(t *testing.T) {
	if err := Init(); err != nil {
		t.Fatal(err)
	}
	if !AllVerified() {
		t.Fatalf("components not verified: %+v", Components())
	}
	langs := map[string]bool{}
	for _, c := range Components() {
		langs[c.Language] = c.Loaded && c.Verified
	}
	for _, l := range []string{"c", "c++", "rust"} {
		if !langs[l] {
			t.Fatalf("component %s not loaded+verified", l)
		}
	}
}

func TestRustValidationThroughCgo(t *testing.T) {
	_ = Init()
	id := "go-test-call-1"
	if SessionOpen(id, 5, 6, false) != OK {
		t.Fatal("open")
	}
	defer SessionClose(id)
	if rc := CheckSignal(id, 6, "offer", sdp); rc != ErrTypeForbidden {
		t.Fatalf("callee offer must be refused, got %d", rc)
	}
	if rc := CheckSignal(id, 5, "offer", sdp); rc != OK {
		t.Fatalf("caller offer: %s", CodeName(rc))
	}
	if rc := CheckSignal(id, 5, "ice-candidate", "not a candidate"); rc != ErrBadCandidate {
		t.Fatalf("bad candidate: %s", CodeName(rc))
	}
	before := Components()[0].Detail["signals_checked"].(uint64)
	CheckSignal(id, 5, "ready", "")
	if Components()[0].Detail["signals_checked"].(uint64) != before+1 {
		t.Fatal("rust counter did not advance: component not on the live path")
	}
}

func TestCppQualityEngineThroughCgo(t *testing.T) {
	_ = Init()
	q, err := NewQuality()
	if err != nil {
		t.Fatal(err)
	}
	defer q.Free()
	for i := 0; i < 6; i++ {
		if err := q.Add(Sample{RTTMs: 900, JitterMs: 200, LossPermille: 300, BitrateKbps: 50, AudioPeak: 0.2}); err != nil {
			t.Fatal(err)
		}
	}
	r, _ := q.Report()
	if r.Level != 3 || !r.AdviseAudioOnly || r.JBTargetMs != 400 {
		t.Fatalf("report %+v", r)
	}
	if err := q.Add(Sample{RTTMs: -5}); err == nil {
		t.Fatal("invalid sample must be refused")
	}
}
