package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const testOrigin = "https://home.example"

func writeTestBackground(t *testing.T, directory, name string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(directory, name), []byte("image"), 0o600); err != nil {
		t.Fatalf("write background %s: %v", name, err)
	}
}

func newTestStore(t *testing.T) (*stateStore, string, string) {
	t.Helper()
	root := t.TempDir()
	backgrounds := filepath.Join(root, "backgrounds")
	stateDir := filepath.Join(root, "state")
	if err := os.MkdirAll(backgrounds, 0o700); err != nil {
		t.Fatal(err)
	}
	writeTestBackground(t, backgrounds, defaultBackground)
	writeTestBackground(t, backgrounds, "test.jpg")
	writeTestBackground(t, backgrounds, "portrait.png")
	clock := func() time.Time {
		return time.Date(2026, 7, 14, 12, 0, 0, 0, time.UTC)
	}
	store, err := newStateStore(backgrounds, filepath.Join(stateDir, "state.json"), clock)
	if err != nil {
		t.Fatalf("new state store: %v", err)
	}
	return store, backgrounds, stateDir
}

func newTestAPI(t *testing.T) (*api, *stateStore) {
	t.Helper()
	store, _, _ := newTestStore(t)
	handler, err := newAPI(store, []string{testOrigin}, log.New(io.Discard, "", 0))
	if err != nil {
		t.Fatal(err)
	}
	return handler, store
}

func patchRequestFor(body string) *http.Request {
	request := httptest.NewRequest(http.MethodPatch, apiPath, strings.NewReader(body))
	request.Header.Set("Origin", testOrigin)
	request.Header.Set("X-Requested-With", "homelab-dashboard")
	request.Header.Set("Content-Type", "application/json")
	return request
}

func decodeResponse[T any](t *testing.T, recorder *httptest.ResponseRecorder) T {
	t.Helper()
	var value T
	if err := json.Unmarshal(recorder.Body.Bytes(), &value); err != nil {
		t.Fatalf("decode response %q: %v", recorder.Body.String(), err)
	}
	return value
}

func TestParseConfig(t *testing.T) {
	cfg, err := parseConfig([]string{
		"--background-dir", `C:\site\backgrounds`,
		"--allowed-origin", "https://home.example,https://backup.example",
		"--allowed-origin", "http://127.0.0.1:8000",
		"--listen", "127.0.0.1:9000",
		"--state-dir", `C:\ProgramData\WallpaperState`,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.allowedOrigin) != 3 {
		t.Fatalf("got %d origins, want 3", len(cfg.allowedOrigin))
	}
	if cfg.listen != "127.0.0.1:9000" {
		t.Fatalf("unexpected listen address %q", cfg.listen)
	}
}

func TestParseConfigRejectsUnsafeListenAndMissingOrigin(t *testing.T) {
	tests := [][]string{
		{"--background-dir", "backgrounds", "--allowed-origin", testOrigin, "--listen", "0.0.0.0:8787"},
		{"--background-dir", "backgrounds"},
	}
	for _, args := range tests {
		if _, err := parseConfig(args); err == nil {
			t.Fatalf("parseConfig(%q) unexpectedly succeeded", args)
		}
	}
}

func TestInitialStateAndGET(t *testing.T) {
	handler, _ := newTestAPI(t)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, apiPath, nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("GET status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	state := decodeResponse[preferences](t, recorder)
	if state.Version != stateVersion || state.Landscape != defaultBackground || state.Portrait != defaultBackground {
		t.Fatalf("unexpected initial state: %+v", state)
	}
	if _, err := time.Parse(time.RFC3339Nano, state.UpdatedAt); err != nil {
		t.Fatalf("updatedAt is not RFC3339: %q", state.UpdatedAt)
	}
	if got := recorder.Header().Get("Cache-Control"); !strings.Contains(got, "no-store") {
		t.Fatalf("Cache-Control = %q", got)
	}
}

func TestPatchUpdatesIndependentSlotsAndPersists(t *testing.T) {
	handler, store := newTestAPI(t)
	for _, body := range []string{
		`{"mode":"landscape","background":"test.jpg"}`,
		`{"mode":"portrait","background":"portrait.png"}`,
	} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, patchRequestFor(body))
		if recorder.Code != http.StatusOK {
			t.Fatalf("PATCH status = %d, body = %s", recorder.Code, recorder.Body.String())
		}
	}
	state, err := store.get()
	if err != nil {
		t.Fatal(err)
	}
	if state.Landscape != "test.jpg" || state.Portrait != "portrait.png" {
		t.Fatalf("unexpected state: %+v", state)
	}
	data, err := os.ReadFile(store.stateFile)
	if err != nil {
		t.Fatal(err)
	}
	var persisted preferences
	if err := json.Unmarshal(data, &persisted); err != nil {
		t.Fatalf("persisted state is invalid JSON: %v", err)
	}
	if persisted != state {
		t.Fatalf("persisted state %+v differs from memory %+v", persisted, state)
	}
}

func TestPatchValidation(t *testing.T) {
	handler, _ := newTestAPI(t)
	oversized := `{"mode":"landscape","background":"test.jpg","padding":"` + strings.Repeat("x", maxPatchBody) + `"}`
	tests := []struct {
		name   string
		mutate func(*http.Request)
		body   string
		status int
	}{
		{name: "missing origin", body: `{"mode":"landscape","background":"test.jpg"}`, mutate: func(r *http.Request) { r.Header.Del("Origin") }, status: http.StatusForbidden},
		{name: "wrong origin", body: `{"mode":"landscape","background":"test.jpg"}`, mutate: func(r *http.Request) { r.Header.Set("Origin", "https://evil.example") }, status: http.StatusForbidden},
		{name: "missing requested with", body: `{"mode":"landscape","background":"test.jpg"}`, mutate: func(r *http.Request) { r.Header.Del("X-Requested-With") }, status: http.StatusForbidden},
		{name: "wrong content type", body: `{"mode":"landscape","background":"test.jpg"}`, mutate: func(r *http.Request) { r.Header.Set("Content-Type", "text/plain") }, status: http.StatusUnsupportedMediaType},
		{name: "invalid mode", body: `{"mode":"desktop","background":"test.jpg"}`, status: http.StatusBadRequest},
		{name: "path traversal", body: `{"mode":"landscape","background":"../test.jpg"}`, status: http.StatusBadRequest},
		{name: "unsupported extension", body: `{"mode":"landscape","background":"test.svg"}`, status: http.StatusBadRequest},
		{name: "missing file", body: `{"mode":"landscape","background":"missing.jpg"}`, status: http.StatusNotFound},
		{name: "unknown field", body: `{"mode":"landscape","background":"test.jpg","extra":true}`, status: http.StatusBadRequest},
		{name: "two objects", body: `{"mode":"landscape","background":"test.jpg"}{}`, status: http.StatusBadRequest},
		{name: "oversized", body: oversized, status: http.StatusRequestEntityTooLarge},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := patchRequestFor(test.body)
			if test.mutate != nil {
				test.mutate(request)
			}
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, request)
			if recorder.Code != test.status {
				t.Fatalf("status = %d, want %d, body = %s", recorder.Code, test.status, recorder.Body.String())
			}
		})
	}
}

func TestDeletedSelectionFallsBackAndRepairsState(t *testing.T) {
	store, backgrounds, _ := newTestStore(t)
	if _, err := store.update("landscape", "test.jpg"); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(backgrounds, "test.jpg")); err != nil {
		t.Fatal(err)
	}
	state, err := store.get()
	if err != nil {
		t.Fatal(err)
	}
	if state.Landscape != defaultBackground {
		t.Fatalf("landscape = %q, want %q", state.Landscape, defaultBackground)
	}
	data, err := os.ReadFile(store.stateFile)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(data, []byte(`"landscape": "bg.jpg"`)) {
		t.Fatalf("state file was not repaired: %s", data)
	}
}

func TestConcurrentUpdatesLeaveCompleteState(t *testing.T) {
	store, backgrounds, stateDir := newTestStore(t)
	writeTestBackground(t, backgrounds, "alternate.webp")
	backgroundsToUse := []string{"test.jpg", "portrait.png", "alternate.webp", defaultBackground}
	var wait sync.WaitGroup
	errorsSeen := make(chan error, 64)
	for index := 0; index < 64; index++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			mode := "landscape"
			if index%2 != 0 {
				mode = "portrait"
			}
			_, err := store.update(mode, backgroundsToUse[index%len(backgroundsToUse)])
			if err != nil {
				errorsSeen <- err
			}
		}(index)
	}
	wait.Wait()
	close(errorsSeen)
	for err := range errorsSeen {
		t.Errorf("concurrent update failed: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(stateDir, "state.json"))
	if err != nil {
		t.Fatal(err)
	}
	var state preferences
	if err := json.Unmarshal(data, &state); err != nil {
		t.Fatalf("state file is incomplete: %v\n%s", err, data)
	}
	entries, err := os.ReadDir(stateDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".state-") {
			t.Fatalf("temporary state file was left behind: %s", entry.Name())
		}
	}
}

func TestHealthAndMethodHandling(t *testing.T) {
	handler, _ := newTestAPI(t)
	tests := []struct {
		method string
		path   string
		status int
	}{
		{http.MethodGet, healthPath, http.StatusOK},
		{http.MethodPost, healthPath, http.StatusMethodNotAllowed},
		{http.MethodPost, apiPath, http.StatusMethodNotAllowed},
		{http.MethodGet, "/missing", http.StatusNotFound},
	}
	for _, test := range tests {
		t.Run(fmt.Sprintf("%s %s", test.method, test.path), func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(test.method, test.path, nil))
			if recorder.Code != test.status {
				t.Fatalf("status = %d, want %d", recorder.Code, test.status)
			}
		})
	}
}
