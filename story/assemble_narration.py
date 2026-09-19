#!/usr/bin/env python3
"""Fit generated chapter audio to the storyboard and derive aligned captions.
No API access; all processing is local. Keeps originals for editorial revision.
"""
import argparse
import datetime
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def stamp(seconds):
    ms=round(seconds*1000)
    return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d}.{ms%1000:03d}'


DURATION = 255  # kept in sync with scene.mjs by the re-timing step

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=ROOT/'narration-work')
    parser.add_argument('--output',type=Path,default=ROOT/'assets/narration')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    work=args.source/'assembled'
    work.mkdir(parents=True,exist_ok=True)
    sources=[]
    for i in range(11):
        found=list(args.source.glob(f'chapter-{i:02d}-*.json'))
        if len(found)!=1:
            raise SystemExit(f'Chapter {i}: expected exactly one source take, found {len(found)}')
        sources.append(found[0])
    cues,edits,segments=[],[],[]
    for source in sources:
        data=json.loads(source.read_text())
        audio=source.with_suffix('.mp3')
        duration=float(run('ffprobe','-v','error','-show_entries','format=duration',
            '-of','default=noprint_wrappers=1:nokey=1',str(audio)))
        slot=data['end']-data['start']
        lead=0.22
        available=slot-lead-0.3
        tempo=max(1.0,duration/available)
        if tempo>1.15:
            raise SystemExit(f'Chapter {data["chapter"]} needs {tempo:.3f}x speed; revise timing instead of rushing speech')
        segment=work/f'{data["chapter"]:02d}.wav'
        run('ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(audio),
            '-af',f'atempo={tempo:.9f},adelay={round(lead*1000)},apad,atrim=duration={slot}',
            '-ar','48000','-ac','1','-c:a','pcm_s16le',str(segment))
        segments.append(segment)
        alignment=data.get('normalized_alignment') or data['alignment']
        chars=alignment['characters'];start=alignment['character_start_times_seconds'];end=alignment['character_end_times_seconds']
        assert len(chars)==len(start)==len(end)
        # Sentence boundaries preserve natural pauses and use actual API timing.
        first=0
        for j,char in enumerate(chars):
            if char in '.!?' or j==len(chars)-1:
                while first<j and chars[first].isspace(): first+=1
                value=''.join(chars[first:j+1]).strip()
                value=value.replace('T L A plus','TLA+').replace('I D','ID')
                if value:
                    at=data['start']+lead+start[first]/tempo
                    until=min(data['end']-.05,data['start']+lead+end[j]/tempo+.12)
                    assert until>at
                    cues.append({'start':round(at,3),'end':round(until,3),'text':value,'chapter':data['chapter']})
                first=j+1
        edits.append({'chapter':data['chapter'],'original_seconds':duration,'slot_seconds':slot,
            'tempo':round(tempo,6),'lead_seconds':lead,'voice_id':data['voice_id'],
            'model':data['model'],'seed':data['seed'],'settings':data['settings']})
        print(f'Chapter {data["chapter"]}: {duration:.2f}s into {slot}s, tempo {tempo:.3f}',flush=True)
    concat=work/'concat.txt'
    # Paths are program-owned local filenames, never user text or credentials.
    concat.write_text(''.join(f"file '{p.as_posix()}'\n" for p in segments))
    master=work/'master.wav'
    run('ffmpeg','-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(concat),
        '-af',f'loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=duration={DURATION}',
        '-ar','48000','-ac','1','-c:a','pcm_s16le',str(master))
    run('ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(master),'-c:a','libmp3lame','-b:a','192k',str(args.output/'jev-story.mp3'))
    (args.output/'captions.json').write_text(json.dumps(cues,indent=2)+'\n')
    (args.output/'jev-story.vtt').write_text('WEBVTT\n\n'+'\n'.join(f'{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}\n' for c in cues))
    (args.output/'provenance.json').write_text(json.dumps({'provider':'ElevenLabs','voice':'George',
        'voice_id':edits[0]['voice_id'],'model':edits[0]['model'],'synthetic':True,
        'duration':DURATION,'generation_date':datetime.date.today().isoformat(),'caption_source':'ElevenLabs character alignment',
        'loudness_target_lufs':-16,'edits':edits},indent=2)+'\n')
    print(f'Assembled full-length narration and {len(cues)} aligned captions in {args.output}',flush=True)


if __name__=='__main__':
    main()
