package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	apiPath           = "/api/background-preferences"
	healthPath        = "/healthz"
	defaultBackground = "bg.jpg"
	maxPatchBody      = 4 * 1024
	stateVersion      = 1
)

var (
	errInvalidBackground = errors.New("invalid background filename")
	errBackgroundMissing = errors.New("background file does not exist")
)

type config struct {
	backgroundDir string
	allowedOrigin []string
	listen        string
	stateDir      string
}

type originFlag struct {
	values []string
}

func (f *originFlag) String() string {
	return strings.Join(f.values, ",")
}

func (f *originFlag) Set(value string) error {
	for _, candidate := range strings.Split(value, ",") {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" {
			return errors.New("allowed origin cannot be empty")
		}
		f.values = append(f.values, candidate)
	}
	return nil
}

func defaultStateDir() string {
	if programData := strings.TrimSpace(os.Getenv("ProgramData")); programData != "" {
		return filepath.Join(programData, "HomeLabWallpaperState")
	}
	return filepath.Join(os.TempDir(), "HomeLabWallpaperState")
}

func parseConfig(args []string) (config, error) {
	var origins originFlag
	cfg := config{}
	flags := flag.NewFlagSet("homelab-wallpaper-state", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	flags.StringVar(&cfg.backgroundDir, "background-dir", "", "path to the public backgrounds directory (required)")
	flags.Var(&origins, "allowed-origin", "allowed dashboard origin; repeat the flag or use a comma-separated list (required)")
	flags.StringVar(&cfg.listen, "listen", "127.0.0.1:8787", "loopback listen address")
	flags.StringVar(&cfg.stateDir, "state-dir", defaultStateDir(), "directory containing state.json and service.log")
	if err := flags.Parse(args); err != nil {
		return config{}, err
	}
	if flags.NArg() != 0 {
		return config{}, fmt.Errorf("unexpected positional arguments: %s", strings.Join(flags.Args(), " "))
	}
	cfg.backgroundDir = strings.TrimSpace(cfg.backgroundDir)
	cfg.stateDir = strings.TrimSpace(cfg.stateDir)
	if cfg.backgroundDir == "" {
		return config{}, errors.New("--background-dir is required")
	}
	if cfg.stateDir == "" {
		return config{}, errors.New("--state-dir cannot be empty")
	}
	if len(origins.values) == 0 {
		return config{}, errors.New("at least one --allowed-origin is required")
	}
	if err := validateListenAddress(cfg.listen); err != nil {
		return config{}, err
	}
	if _, err := parseAllowedOrigins(origins.values); err != nil {
		return config{}, err
	}
	cfg.allowedOrigin = append([]string(nil), origins.values...)
	return cfg, nil
}

func validateListenAddress(address string) error {
	host, portText, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf("invalid --listen address: %w", err)
	}
	if host != "127.0.0.1" {
		return errors.New("--listen must bind to 127.0.0.1")
	}
	port, err := strconv.Atoi(portText)
	if err != nil || port < 1 || port > 65535 {
		return errors.New("--listen must include a port from 1 to 65535")
	}
	return nil
}

func normalizeOrigin(raw string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return "", err
	}
	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", errors.New("origin must use http or https")
	}
	if parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", errors.New("origin must contain only scheme, host, and optional port")
	}
	if parsed.Path != "" && parsed.Path != "/" {
		return "", errors.New("origin must not contain a path")
	}
	return scheme + "://" + strings.ToLower(parsed.Host), nil
}

func parseAllowedOrigins(values []string) (map[string]struct{}, error) {
	allowed := make(map[string]struct{}, len(values))
	for _, raw := range values {
		origin, err := normalizeOrigin(raw)
		if err != nil {
			return nil, fmt.Errorf("invalid --allowed-origin %q: %w", raw, err)
		}
		allowed[origin] = struct{}{}
	}
	return allowed, nil
}

type preferences struct {
	Version   int    `json:"version"`
	Landscape string `json:"landscape"`
	Portrait  string `json:"portrait"`
	UpdatedAt string `json:"updatedAt"`
}

type stateStore struct {
	mu            sync.Mutex
	backgroundDir string
	stateFile     string
	state         preferences
	now           func() time.Time
}

func newStateStore(backgroundDir, stateFile string, now func() time.Time) (*stateStore, error) {
	info, err := os.Stat(backgroundDir)
	if err != nil {
		return nil, fmt.Errorf("inspect background directory: %w", err)
	}
	if !info.IsDir() {
		return nil, errors.New("background path is not a directory")
	}
	if now == nil {
		now = time.Now
	}
	store := &stateStore{
		backgroundDir: filepath.Clean(backgroundDir),
		stateFile:     filepath.Clean(stateFile),
		now:           now,
	}
	if err := store.loadOrInitialize(); err != nil {
		return nil, err
	}
	return store, nil
}

func validateBackgroundName(name string) error {
	if name == "" || strings.TrimSpace(name) != name {
		return errInvalidBackground
	}
	if name == "." || name == ".." || strings.ContainsAny(name, "/\\\\:\x00") {
		return errInvalidBackground
	}
	if filepath.Base(name) != name || filepath.Clean(name) != name {
		return errInvalidBackground
	}
	switch strings.ToLower(filepath.Ext(name)) {
	case ".jpg", ".jpeg", ".png", ".webp", ".avif":
		return nil
	default:
		return errInvalidBackground
	}
}

func (s *stateStore) requireBackground(name string) error {
	if err := validateBackgroundName(name); err != nil {
		return err
	}
	info, err := os.Lstat(filepath.Join(s.backgroundDir, name))
	if errors.Is(err, os.ErrNotExist) {
		return errBackgroundMissing
	}
	if err != nil {
		return fmt.Errorf("inspect background: %w", err)
	}
	if !info.Mode().IsRegular() {
		return errBackgroundMissing
	}
	return nil
}

func decodePreferences(data []byte, target *preferences) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("state file contains more than one JSON value")
		}
		return err
	}
	return nil
}

func (s *stateStore) timestamp() string {
	return s.now().UTC().Format(time.RFC3339Nano)
}

func (s *stateStore) loadOrInitialize() error {
	data, err := os.ReadFile(s.stateFile)
	if errors.Is(err, os.ErrNotExist) {
		if err := s.requireBackground(defaultBackground); err != nil {
			return fmt.Errorf("default background %q is unavailable: %w", defaultBackground, err)
		}
		s.state = preferences{
			Version:   stateVersion,
			Landscape: defaultBackground,
			Portrait:  defaultBackground,
			UpdatedAt: s.timestamp(),
		}
		if err := s.writeLocked(); err != nil {
			return fmt.Errorf("initialize state file: %w", err)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("read state file: %w", err)
	}
	var loaded preferences
	if err := decodePreferences(data, &loaded); err != nil {
		return fmt.Errorf("decode state file: %w", err)
	}
	if loaded.Version != stateVersion {
		return fmt.Errorf("unsupported state version %d", loaded.Version)
	}
	s.state = loaded
	changed, err := s.healLocked()
	if err != nil {
		return err
	}
	if _, err := time.Parse(time.RFC3339Nano, s.state.UpdatedAt); err != nil {
		changed = true
	}
	if changed {
		s.state.UpdatedAt = s.timestamp()
		if err := s.writeLocked(); err != nil {
			return fmt.Errorf("repair state file: %w", err)
		}
	}
	return nil
}

func (s *stateStore) healLocked() (bool, error) {
	if err := s.requireBackground(defaultBackground); err != nil {
		return false, fmt.Errorf("default background %q is unavailable: %w", defaultBackground, err)
	}
	changed := false
	for _, selected := range []*string{&s.state.Landscape, &s.state.Portrait} {
		err := s.requireBackground(*selected)
		if errors.Is(err, errInvalidBackground) || errors.Is(err, errBackgroundMissing) {
			*selected = defaultBackground
			changed = true
			continue
		}
		if err != nil {
			return false, err
		}
	}
	return changed, nil
}

func (s *stateStore) get() (preferences, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	previous := s.state
	changed, err := s.healLocked()
	if err != nil {
		return preferences{}, err
	}
	if changed {
		s.state.UpdatedAt = s.timestamp()
		if err := s.writeLocked(); err != nil {
			s.state = previous
			return preferences{}, err
		}
	}
	return s.state, nil
}

func (s *stateStore) update(mode, background string) (preferences, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.requireBackground(background); err != nil {
		return preferences{}, err
	}
	previous := s.state
	changed, err := s.healLocked()
	if err != nil {
		return preferences{}, err
	}
	switch mode {
	case "landscape":
		if s.state.Landscape != background {
			s.state.Landscape = background
			changed = true
		}
	case "portrait":
		if s.state.Portrait != background {
			s.state.Portrait = background
			changed = true
		}
	default:
		return preferences{}, errors.New("mode must be landscape or portrait")
	}
	if !changed {
		return s.state, nil
	}
	s.state.UpdatedAt = s.timestamp()
	if err := s.writeLocked(); err != nil {
		s.state = previous
		return preferences{}, err
	}
	return s.state, nil
}

func (s *stateStore) writeLocked() error {
	directory := filepath.Dir(s.stateFile)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(s.state, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	temporary, err := os.CreateTemp(directory, ".state-*.tmp")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	removeTemporary := true
	defer func() {
		if removeTemporary {
			_ = os.Remove(temporaryName)
		}
	}()
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return err
	}
	if _, err := temporary.Write(data); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryName, s.stateFile); err != nil {
		return err
	}
	removeTemporary = false
	return nil
}

type api struct {
	store          *stateStore
	allowedOrigins map[string]struct{}
	logger         *log.Logger
}

func newAPI(store *stateStore, allowedOrigin []string, logger *log.Logger) (*api, error) {
	allowed, err := parseAllowedOrigins(allowedOrigin)
	if err != nil {
		return nil, err
	}
	if len(allowed) == 0 {
		return nil, errors.New("at least one allowed origin is required")
	}
	if logger == nil {
		logger = log.New(io.Discard, "", 0)
	}
	return &api{store: store, allowedOrigins: allowed, logger: logger}, nil
}

func (a *api) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store, max-age=0")
	w.Header().Set("Pragma", "no-cache")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	switch r.URL.Path {
	case apiPath:
		a.handlePreferences(w, r)
	case healthPath:
		a.handleHealth(w, r)
	default:
		a.writeError(w, http.StatusNotFound, "not found")
	}
}

func (a *api) handlePreferences(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		state, err := a.store.get()
		if err != nil {
			a.internalError(w, r, err)
			return
		}
		a.writeJSON(w, http.StatusOK, state)
	case http.MethodPatch:
		a.handlePatch(w, r)
	default:
		w.Header().Set("Allow", "GET, PATCH")
		a.writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (a *api) originAllowed(r *http.Request) bool {
	origins := r.Header.Values("Origin")
	if len(origins) != 1 {
		return false
	}
	origin, err := normalizeOrigin(origins[0])
	if err != nil {
		return false
	}
	_, ok := a.allowedOrigins[origin]
	return ok
}

type patchRequest struct {
	Mode       string `json:"mode"`
	Background string `json:"background"`
}

func (a *api) handlePatch(w http.ResponseWriter, r *http.Request) {
	if !a.originAllowed(r) {
		a.writeError(w, http.StatusForbidden, "origin is not allowed")
		return
	}
	if r.Header.Get("X-Requested-With") != "homelab-dashboard" {
		a.writeError(w, http.StatusForbidden, "missing dashboard request header")
		return
	}
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || !strings.EqualFold(mediaType, "application/json") {
		a.writeError(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json")
		return
	}
	if r.ContentLength > maxPatchBody {
		a.writeError(w, http.StatusRequestEntityTooLarge, "request body is too large")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxPatchBody+1))
	if err != nil {
		a.writeError(w, http.StatusBadRequest, "unable to read request body")
		return
	}
	if len(body) > maxPatchBody {
		a.writeError(w, http.StatusRequestEntityTooLarge, "request body is too large")
		return
	}
	var request patchRequest
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		a.writeError(w, http.StatusBadRequest, "invalid JSON request")
		return
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		a.writeError(w, http.StatusBadRequest, "request must contain one JSON object")
		return
	}
	if request.Mode != "landscape" && request.Mode != "portrait" {
		a.writeError(w, http.StatusBadRequest, "mode must be landscape or portrait")
		return
	}
	state, err := a.store.update(request.Mode, request.Background)
	switch {
	case errors.Is(err, errInvalidBackground):
		a.writeError(w, http.StatusBadRequest, "invalid background filename")
		return
	case errors.Is(err, errBackgroundMissing):
		a.writeError(w, http.StatusNotFound, "background file does not exist")
		return
	case err != nil:
		a.internalError(w, r, err)
		return
	}
	a.writeJSON(w, http.StatusOK, state)
}

func (a *api) handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", "GET")
		a.writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, err := a.store.get(); err != nil {
		a.logger.Printf("health check failed: %v", err)
		a.writeError(w, http.StatusServiceUnavailable, "unhealthy")
		return
	}
	a.writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (a *api) internalError(w http.ResponseWriter, r *http.Request, err error) {
	a.logger.Printf("%s %s failed: %v", r.Method, r.URL.Path, err)
	a.writeError(w, http.StatusInternalServerError, "internal server error")
}

func (a *api) writeError(w http.ResponseWriter, status int, message string) {
	a.writeJSON(w, status, map[string]string{"error": message})
}

func (a *api) writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		a.logger.Printf("write response failed: %v", err)
	}
}

func openLogger(stateDir string) (*log.Logger, io.Closer, error) {
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return nil, nil, err
	}
	file, err := os.OpenFile(filepath.Join(stateDir, "service.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, nil, err
	}
	return log.New(io.MultiWriter(os.Stdout, file), "", log.Ldate|log.Ltime|log.LUTC), file, nil
}

func run(args []string) error {
	cfg, err := parseConfig(args)
	if err != nil {
		return fmt.Errorf("configuration: %w", err)
	}
	logger, closer, err := openLogger(cfg.stateDir)
	if err != nil {
		return fmt.Errorf("open service log: %w", err)
	}
	defer closer.Close()
	store, err := newStateStore(cfg.backgroundDir, filepath.Join(cfg.stateDir, "state.json"), time.Now)
	if err != nil {
		logger.Printf("fatal: initialize state: %v", err)
		return fmt.Errorf("initialize state: %w", err)
	}
	handler, err := newAPI(store, cfg.allowedOrigin, logger)
	if err != nil {
		logger.Printf("fatal: initialize API: %v", err)
		return fmt.Errorf("initialize API: %w", err)
	}
	server := &http.Server{
		Addr:              cfg.listen,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    16 * 1024,
	}
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	defer signal.Stop(interrupt)
	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		<-interrupt
		logger.Printf("shutdown requested")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			logger.Printf("graceful shutdown failed: %v", err)
		}
	}()
	logger.Printf("wallpaper state service listening on %s", cfg.listen)
	err = server.ListenAndServe()
	if errors.Is(err, http.ErrServerClosed) {
		<-shutdownDone
		return nil
	}
	logger.Printf("fatal: HTTP server stopped: %v", err)
	return err
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "homelab-wallpaper-state: %v\n", err)
		os.Exit(1)
	}
}
