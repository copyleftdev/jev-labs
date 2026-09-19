#!/usr/bin/env python3
"""Generate narration for the Jev story with ElevenLabs; credentials never enter web assets.

Reads a dotenv key without executing shell code. Generation is resumable and
cached by payload hash to avoid accidental duplicate paid requests.
"""
import argparse
import base64
import hashlib
import json
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = 'https://api.elevenlabs.io'


def key_from_file(path):
    for line in path.read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?(ELEVEN_LABS_API_KEY|ELEVENLABS_API_KEY)\s*=\s*(.*)$', line)
        if match:
            values = shlex.split(match.group(2), comments=True)
            if len(values) == 1 and values[0]:
                return values[0]
    raise SystemExit('No ElevenLabs API key found in the credential file')


def request(key, endpoint, payload=None):
    req = urllib.request.Request(BASE + endpoint,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'xi-api-key':key, 'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never log request headers or credential values.
        raise SystemExit(f'ElevenLabs returned HTTP {error.code}; generation stopped without retry') from None
    except urllib.error.URLError:
        raise SystemExit('ElevenLabs network connection failed; no automatic retry') from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', type=Path, default=Path.home()/'.creds/eleven.env')
    parser.add_argument('--list-voices', action='store_true')
    parser.add_argument('--voice', help='An available premade narrator voice ID')
    parser.add_argument('--chapter', type=int, help='Generate only one chapter, 0..9')
    parser.add_argument('--speed', type=float, default=0.95)
    parser.add_argument('--output', type=Path, default=ROOT/'narration-work')
    args = parser.parse_args()
    key = key_from_file(args.credentials)
    if args.list_voices:
        data = request(key, '/v2/voices?page_size=100&voice_type=default')
        for voice in data.get('voices', []):
            print(json.dumps({k:voice.get(k) for k in ['voice_id','name','category','description','labels']}))
        return
    if not args.voice:
        parser.error('--voice is required for generation')
    if not re.fullmatch(r'[A-Za-z0-9]{10,64}', args.voice):
        parser.error('Invalid voice ID')
    if not 0.7 <= args.speed <= 1.2 or (args.chapter is not None and not 0 <= args.chapter <= 9):
        parser.error('Invalid speed or chapter')
    chapters = json.loads(subprocess.check_output(['node','--input-type=module','-e',
        'import {CHAPTERS} from "./scene.mjs"; console.log(JSON.stringify(CHAPTERS));'],cwd=ROOT,text=True))
    args.output.mkdir(parents=True, exist_ok=True)
    for index, chapter in enumerate(chapters):
        if args.chapter is not None and index != args.chapter:
            continue
        # Spoken acronyms are spaced explicitly; the written script is retained.
        spoken = re.sub(r'\bJev\b', 'Jev', chapter['transcript'])
        spoken = re.sub(r'\bTLA\+', 'T L A plus', spoken)
        spoken = re.sub(r'\bID\b', 'I D', spoken)
        payload = {'text':spoken,'model_id':'eleven_multilingual_v2',
            'voice_settings':{'stability':0.65,'similarity_boost':0.75,'style':0.15,
                              'use_speaker_boost':True,'speed':args.speed},
            'seed':4120926 + index}
        digest = hashlib.sha256(json.dumps([args.voice,payload],sort_keys=True).encode()).hexdigest()[:16]
        prefix = args.output/f'chapter-{index:02d}-{digest}'
        audio_path, timing_path = prefix.with_suffix('.mp3'), prefix.with_suffix('.json')
        if audio_path.exists() and timing_path.exists():
            print(f'Chapter {index}: cached',flush=True)
            continue
        print(f'Chapter {index}: generating {len(spoken)} characters',flush=True)
        result = request(key,f'/v1/text-to-speech/{args.voice}/with-timestamps?output_format=mp3_44100_128',payload)
        audio_path.write_bytes(base64.b64decode(result['audio_base64'],validate=True))
        metadata = {'chapter':index,'voice_id':args.voice,'model':payload['model_id'],
            'settings':payload['voice_settings'],'seed':payload['seed'],'source_text':chapter['transcript'],
            'spoken_text':spoken,'start':chapter['start'],'end':chapter['end'],
            'alignment':result.get('alignment'),'normalized_alignment':result.get('normalized_alignment')}
        timing_path.write_text(json.dumps(metadata,indent=2)+'\n')
        duration = subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration',
            '-of','default=noprint_wrappers=1:nokey=1',str(audio_path)],text=True).strip()
        print(f'Chapter {index}: saved, {duration}s / {chapter["end"]-chapter["start"]}s scene',flush=True)


if __name__ == '__main__':
    main()
