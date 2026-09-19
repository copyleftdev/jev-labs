#!/usr/bin/env python3
"""Capture deterministic frames of the Jev story. Requires Python Playwright and Chromium.
Serve the website separately. Does not call Jev; every frame is a pure function of t.
"""
import argparse
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8765')
parser.add_argument('--output', type=Path, default=Path('captures'))
parser.add_argument('--start', type=float, default=0)
parser.add_argument('--end', type=float, default=None)
parser.add_argument('--fps', type=int, default=30)
parser.add_argument('--width', type=int, default=1920)
parser.add_argument('--height', type=int, default=1080)
parser.add_argument('--browser', help='Optional installed Chromium executable')
parser.add_argument('--video', type=Path, help='Encode directly to MP4 instead of writing PNG frames')
parser.add_argument('--audio', type=Path, help='Local narration to mux with --video')
args = parser.parse_args()
DURATION = float(subprocess.check_output(['node','--input-type=module','-e','import {DURATION} from "./scene.mjs"; console.log(DURATION);'],cwd=Path(__file__).resolve().parent,text=True).strip())
if args.end is None: args.end = DURATION
if not (0 <= args.start < args.end <= DURATION and 1 <= args.fps <= 60 and
        320 <= args.width <= 3840 and 320 <= args.height <= 2160):
    parser.error('Invalid time, frame rate, or dimensions')
if args.audio and not args.video:
    parser.error('--audio requires --video')
if args.video and args.video.exists():
    parser.error('Video output exists; choose a new filename')
args.output.mkdir(parents=True, exist_ok=True)
frames = round((args.end - args.start) * args.fps)
encoder, encoder_log = None, None
try:
    if args.video:
        args.video.parent.mkdir(parents=True, exist_ok=True)
        cmd=['ffmpeg','-hide_banner','-loglevel','error','-f','image2pipe','-framerate',str(args.fps),'-i','pipe:0']
        if args.audio:
            cmd+=['-ss',str(args.start),'-i',str(args.audio),'-map','0:v:0','-map','1:a:0','-c:a','aac','-b:a','192k']
        cmd+=['-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
              '-t',str(args.end-args.start),'-movflags','+faststart',str(args.video)]
        encoder_log=open(args.output/'encoder.log','w')
        encoder=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=encoder_log)
    with sync_playwright() as p:
        browser = p.chromium.launch(**({'executable_path':args.browser} if args.browser else {}))
        page = browser.new_page(viewport={'width':args.width,'height':args.height}, device_scale_factor=1)
        page.goto(args.url + '/index.html?capture=1')
        page.wait_for_function('window.story?.ready === true')
        for frame in range(frames):
            page.evaluate('(t) => window.story.seek(t)', args.start + frame / args.fps)
            if encoder:
                encoder.stdin.write(page.screenshot())
            else:
                page.screenshot(path=str(args.output / f'frame-{frame:05d}.png'))
            if frame%300==0:
                print(f'Captured {frame}/{frames} frames',flush=True)
        metadata = page.evaluate('window.story.metadata')
        browser.close()
    if encoder:
        encoder.stdin.close()
        if encoder.wait(timeout=120)!=0:
            raise SystemExit(f'Video encoding failed; inspect {args.output}/encoder.log')
finally:
    if encoder and encoder.poll() is None:
        encoder.terminate()
        try:
            encoder.wait(timeout=10)
        except subprocess.TimeoutExpired:
            encoder.kill()
            encoder.wait(timeout=5)
    if encoder_log:
        encoder_log.close()
(args.output/'capture.json').write_text(json.dumps({**metadata, 'start':args.start,'end':args.end,
    'fps':args.fps,'width':args.width,'height':args.height,'frames':frames,
    'video':str(args.video) if args.video else None,'audio':str(args.audio) if args.audio else None}, indent=2)+'\n')
print(f'Captured {frames} frames to {args.output}')
