// hdnative: thin native e-ink client for the Kindle.
//
// The OptiPlex renders every page as a PNG (and remembers its tap zones).
// This program only (1) shows PNGs with fbink, (2) reads raw touch events
// from /dev/input and forwards each tap to the server, (3) polls a change
// stamp so the screen updates when something changes.
//
// Build: GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -ldflags "-s -w"
package main

import (
	"bytes"
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

const (
	evSyn        = 0x00
	evKey        = 0x01
	evAbs        = 0x03
	absX         = 0x00
	absY         = 0x01
	absMtSlot    = 0x2f
	absMtPosX    = 0x35
	absMtPosY    = 0x36
	absMtTrackID = 0x39
	btnTouch     = 0x14a
	synReport    = 0x00
)

var (
	server   = flag.String("server", "http://dash-server.lan:8765", "dashboard server (LAN, tried first)")
	remote   = flag.String("remote", "", "same server over Tailscale, e.g. http://100.x.y.z:8765 (empty = no fallback)")
	proxy    = flag.String("proxy", "http://127.0.0.1:1056", "tailscaled HTTP proxy for the remote route")
	fbink    = flag.String("fbink", "/mnt/us/libkh/bin/fbink", "fbink binary")
	devPath  = flag.String("dev", "", "touch input device (auto-detect if empty)")
	poll     = flag.Duration("poll", 30*time.Second, "stamp poll interval")
	fullEach = flag.Int("full", 8, "flashing refresh every N redraws")
	tmpPNG   = flag.String("tmp", "/tmp/hdnative.png", "temp png path")
	dryRun   = flag.Bool("dry", false, "do not call fbink (host testing)")
)

type absInfo struct{ Value, Min, Max, Fuzz, Flat, Res int32 }

func ioctl(fd uintptr, req uintptr, arg unsafe.Pointer) error {
	_, _, e := syscall.Syscall(syscall.SYS_IOCTL, fd, req, uintptr(arg))
	if e != 0 {
		return e
	}
	return nil
}

// _IOC(dir,type,nr,size)
func ioc(dir, typ, nr, size uintptr) uintptr { return dir<<30 | size<<16 | typ<<8 | nr }

func evioCGBit(ev, size uintptr) uintptr { return ioc(2, 'E', 0x20+ev, size) }
func evioCGAbs(abs uintptr) uintptr    { return ioc(2, 'E', 0x40+abs, unsafe.Sizeof(absInfo{})) }

func hasBit(b []byte, n int) bool { return n/8 < len(b) && b[n/8]&(1<<(uint(n)%8)) != 0 }

type touchDev struct {
	path       string
	f          *os.File
	xCode      uint16
	yCode      uint16
	xMax, yMax int32
	xMin, yMin int32
}

func openTouch(path string) (*touchDev, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	bits := make([]byte, 16)
	if err := ioctl(f.Fd(), evioCGBit(evAbs, uintptr(len(bits))), unsafe.Pointer(&bits[0])); err != nil {
		f.Close()
		return nil, err
	}
	t := &touchDev{path: path, f: f}
	switch {
	case hasBit(bits, absMtPosX) && hasBit(bits, absMtPosY):
		t.xCode, t.yCode = absMtPosX, absMtPosY
	case hasBit(bits, absX) && hasBit(bits, absY):
		t.xCode, t.yCode = absX, absY
	default:
		f.Close()
		return nil, fmt.Errorf("no abs xy")
	}
	var ax, ay absInfo
	if err := ioctl(f.Fd(), evioCGAbs(uintptr(t.xCode)), unsafe.Pointer(&ax)); err != nil {
		f.Close()
		return nil, err
	}
	if err := ioctl(f.Fd(), evioCGAbs(uintptr(t.yCode)), unsafe.Pointer(&ay)); err != nil {
		f.Close()
		return nil, err
	}
	t.xMin, t.xMax, t.yMin, t.yMax = ax.Min, ax.Max, ay.Min, ay.Max
	if t.xMax <= t.xMin || t.yMax <= t.yMin {
		f.Close()
		return nil, fmt.Errorf("bad abs range")
	}
	return t, nil
}

func findTouch() (*touchDev, error) {
	if *devPath != "" {
		return openTouch(*devPath)
	}
	paths, _ := filepath.Glob("/dev/input/event*")
	var best *touchDev
	for _, p := range paths {
		t, err := openTouch(p)
		if err != nil {
			continue
		}
		// prefer multitouch devices
		if best == nil || (t.xCode == absMtPosX && best.xCode != absMtPosX) {
			if best != nil {
				best.f.Close()
			}
			best = t
		} else {
			t.f.Close()
		}
	}
	if best == nil {
		return nil, fmt.Errorf("no touch device in %v", paths)
	}
	return best, nil
}

type tap struct{ x, y int32 }

// readTaps emits one tap per finger lift (last known position).
func (t *touchDev) readTaps(out chan<- tap) {
	evSize := 16 // 32-bit ARM: timeval(8) + type(2) + code(2) + value(4)
	if runtime.GOARCH == "amd64" || runtime.GOARCH == "arm64" {
		evSize = 24
	}
	buf := make([]byte, evSize*64)
	var x, y int32 = -1, -1
	down := false
	for {
		n, err := t.f.Read(buf)
		if err != nil {
			log.Printf("touch read error: %v", err)
			time.Sleep(time.Second)
			continue
		}
		for off := 0; off+evSize <= n; off += evSize {
			e := buf[off : off+evSize]
			typ := binary.LittleEndian.Uint16(e[evSize-8:])
			code := binary.LittleEndian.Uint16(e[evSize-6:])
			val := int32(binary.LittleEndian.Uint32(e[evSize-4:]))
			switch typ {
			case evAbs:
				switch code {
				case t.xCode:
					x = val
					down = true
				case t.yCode:
					y = val
					down = true
				case absMtTrackID:
					if val == -1 && down && x >= 0 && y >= 0 {
						out <- tap{x, y}
						down = false
					}
				}
			case evKey:
				if code == btnTouch && val == 0 && down && x >= 0 && y >= 0 {
					out <- tap{x, y}
					down = false
				}
			}
		}
	}
}

type route struct {
	name string
	base string
	hc   *http.Client
}

type app struct {
	mu       sync.Mutex
	page     string
	stamp    string
	redraws  int
	routes   []route
	cur      int
	lastLAN  time.Time
	touch    *touchDev
}

// get tries the current route, then the other one; remembers what worked.
func (a *app) get(pathQuery string) (*http.Response, error) {
	// while on the remote route, re-probe LAN every 2 minutes
	if a.cur != 0 && time.Since(a.lastLAN) > 2*time.Minute {
		a.lastLAN = time.Now()
		quick := &http.Client{Timeout: 3 * time.Second}
		if r, err := quick.Get(a.routes[0].base + "/health"); err == nil {
			r.Body.Close()
			log.Printf("route: back to LAN")
			a.cur = 0
		}
	}
	var lastErr error
	for i := 0; i < len(a.routes); i++ {
		idx := (a.cur + i) % len(a.routes)
		rt := a.routes[idx]
		resp, err := rt.hc.Get(rt.base + pathQuery)
		if err == nil {
			if idx != a.cur {
				log.Printf("route: switched to %s", rt.name)
				a.cur = idx
				if idx != 0 {
					a.lastLAN = time.Now()
				}
			}
			return resp, nil
		}
		lastErr = err
	}
	return nil, lastErr
}

func lipcGet(prop string) string {
	out, err := exec.Command("lipc-get-prop", "com.lab126.powerd", prop).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func (a *app) query(extra url.Values) string {
	v := url.Values{}
	v.Set("page", a.page)
	if b := lipcGet("battLevel"); b != "" {
		v.Set("bat", b)
	}
	if c := lipcGet("isCharging"); c != "" {
		v.Set("chg", c)
	}
	for k, vs := range extra {
		for _, s := range vs {
			v.Add(k, s)
		}
	}
	return v.Encode()
}

// fetch GETs a PNG endpoint and shows it; X-Page / X-Stamp headers update state.
func (a *app) fetch(path string, extra url.Values, flash bool) error {
	resp, err := a.get(path + "?" + a.query(extra))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.Header.Get("X-Exit") == "1" {
		log.Printf("exit requested from server")
		os.Exit(0)
	}
	if resp.StatusCode != 200 {
		return fmt.Errorf("%s: HTTP %d %s", path, resp.StatusCode, bytes.TrimSpace(body[:min(len(body), 120)]))
	}
	newPage := resp.Header.Get("X-Page")
	if newPage != "" && newPage != a.page {
		flash = true
		a.page = newPage
	}
	if s := resp.Header.Get("X-Stamp"); s != "" {
		a.stamp = s
	}
	if resp.Header.Get("X-Flash") == "1" {
		flash = true
	}
	return a.show(body, flash)
}

func (a *app) show(png []byte, flash bool) error {
	a.redraws++
	if a.redraws%*fullEach == 0 {
		flash = true
	}
	if err := os.WriteFile(*tmpPNG, png, 0644); err != nil {
		return err
	}
	if *dryRun {
		log.Printf("show %d bytes flash=%v page=%s", len(png), flash, a.page)
		return nil
	}
	args := []string{"-q", "-g", "file=" + *tmpPNG}
	if flash {
		args = append([]string{"-f"}, args...)
	}
	return exec.Command(*fbink, args...).Run()
}

func (a *app) checkStamp() {
	resp, err := a.get("/n/stamp?page=" + url.QueryEscape(a.page))
	if err != nil {
		log.Printf("stamp: %v", err)
		return
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	s := strings.TrimSpace(string(b))
	if s != "" && s != a.stamp {
		log.Printf("stamp changed %s -> %s", a.stamp, s)
		if err := a.fetch("/n/page", nil, false); err != nil {
			log.Printf("refresh: %v", err)
		}
	}
}

func (a *app) onTap(t tap) {
	v := url.Values{}
	v.Set("rx", strconv.Itoa(int(t.x)))
	v.Set("ry", strconv.Itoa(int(t.y)))
	v.Set("rxmin", strconv.Itoa(int(a.touch.xMin)))
	v.Set("rxmax", strconv.Itoa(int(a.touch.xMax)))
	v.Set("rymin", strconv.Itoa(int(a.touch.yMin)))
	v.Set("rymax", strconv.Itoa(int(a.touch.yMax)))
	if err := a.fetch("/n/tap", v, false); err != nil {
		log.Printf("tap: %v", err)
	}
}

func main() {
	flag.Parse()
	if lf, err := os.OpenFile("/mnt/us/hdnative/log.txt", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644); err == nil {
		log.SetOutput(lf)
	}
	log.SetFlags(log.LstdFlags)
	direct := &http.Transport{Proxy: nil}
	a := &app{page: "home", routes: []route{{"LAN", *server, &http.Client{Timeout: 6 * time.Second, Transport: direct}}}}
	if *remote != "" {
		pu, err := url.Parse(*proxy)
		if err == nil {
			tr := &http.Transport{Proxy: http.ProxyURL(pu)}
			a.routes = append(a.routes, route{"Tailscale", *remote, &http.Client{Timeout: 20 * time.Second, Transport: tr}})
		}
	}

	td, err := findTouch()
	if err != nil {
		log.Printf("touch: %v (display-only mode)", err)
	} else {
		a.touch = td
		log.Printf("touch %s codes x=%#x y=%#x range x[%d,%d] y[%d,%d]", td.path, td.xCode, td.yCode, td.xMin, td.xMax, td.yMin, td.yMax)
	}

	for i := 0; ; i++ {
		if err := a.fetch("/n/page", nil, true); err != nil {
			log.Printf("first fetch: %v", err)
			time.Sleep(time.Duration(min(i+1, 6)) * 5 * time.Second)
			continue
		}
		break
	}

	taps := make(chan tap, 8)
	if a.touch != nil {
		go a.touch.readTaps(taps)
	}
	tick := time.NewTicker(*poll)
	var last time.Time
	for {
		select {
		case t := <-taps:
			if time.Since(last) < 400*time.Millisecond { // debounce
				continue
			}
			last = time.Now()
			// drain taps queued while we were busy rendering
			a.onTap(t)
			for len(taps) > 0 {
				<-taps
			}
		case <-tick.C:
			a.checkStamp()
		}
	}
}
