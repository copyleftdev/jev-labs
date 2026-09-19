// Scene model for "Never confidently wrong". Pure: evaluate(t, data) -> state.
// Motion is physical: springs for arrivals, gravity for the floor chapter,
// noise for chaos. All closed-form in t, so seeking is exact.

import { spring, easeOutExpo, easeInOut, noise1, clamp01 } from './motion.mjs';

// Chapter timing is set by the narration takes (see README). `title` lines
// are kinetic; `transcript` is the voice.
export const CHAPTERS = [
  { start: 0,   end: 16,  name: 'The rule',        title: 'It may decline.\nIt may never be\nconfidently wrong.',
    transcript: 'A pharmacy system gets one rule that matters more than being accurate. It is allowed to say I don\'t know. It is not allowed to be sure, and wrong. We built one on top of Jev. Then we spent a few days trying to break it.' },
  { start: 16,  end: 37,  name: 'A number',        title: 'Not a paragraph.\nA number.',
    transcript: 'Ask Jev whether oxycodone is a controlled substance. You don\'t get a paragraph. You get zero point nine eight. A number has a noise floor you can measure. A paragraph doesn\'t. And once you can measure the noise, you can gate on it, replay it, and prove things about the system around it.' },
  { start: 37,  end: 71,  name: 'What we built',   title: 'A kernel\naround an oracle.',
    transcript: 'So we built a consensus kernel around it. First a TLA plus specification, model checked until the quorum bound fell out of the math instead of a textbook. Then an AsyncAPI contract derived from that spec. Then Rust, generated from the contract, with Jev behind a single trait as the oracle. A record goes in. Five paraphrased questions fan out to Jev. Five probabilities come back. The gate, the quorum, the decision. Every step traceable to a line in the spec.' },
  { start: 71,  end: 95,  name: 'Five voices',     title: 'Five agents.\nThree must agree.',
    transcript: 'Five agents. Each asks a differently worded version of the same question, because five agents on one prompt turned out to be one agent sampled five times. We measured that too. Three of them have to land on the same answer, stably, before the round decides. Every vote carries a request ID you can look up on TypeSafe\'s side.' },
  { start: 95,  end: 123, name: 'The floor',       title: 'The floor\nwas measured,\nnot chosen.',
    transcript: 'Send Jev the identical request over and over, and the answer jitters by about zero point zero four two. That\'s the noise floor, from fourteen hundred repeated calls. Any vote whose margin sits inside that band is noise wearing a verdict. The gate throws it out. Every excluded vote sat below the floor. Every included vote sat above it. We set it once and never touched it.' },
  { start: 123, end: 145, name: 'Chaos',           title: 'Then we broke\neverything.',
    transcript: 'Adversarial text spliced into patient records. Agents crashed mid-round. Calls rate-limited. Records cut off mid-sentence. All of it driven by a seed, so any failure replays exactly. The question was never whether it stays right under that. It\'s what it does when it can\'t be sure.' },
  { start: 145, end: 166, name: 'It declined',     title: 'It declined\na trivial question.',
    transcript: 'Documented penicillin anaphylaxis. A new order for amoxicillin. Under chaos, two agents got rate-limited. A third came back at zero point five four, right on the floor. Quorum not met. So the kernel sent it to a human. It declined a trivial question, and that\'s the design working.' },
  { start: 166, end: 188, name: 'Degrade',         title: 'A lost agent\nis just\na lost agent.',
    transcript: 'Our first version failed fast. A transport error killed the round. Under chaos that voided seventy-one percent of rounds and pushed every one of them to a pharmacist for no clinical reason. Now a lost agent is just a lost agent. Zero rounds aborted. The survivors decide or escalate on their own terms.' },
  { start: 188, end: 209, name: 'Zero',            title: 'Zero.',
    transcript: 'One thousand and eighty golden rounds. Three chaos levels. Forty seeds each. Zero wrong verdicts. I want to be careful with that. Zero observed doesn\'t mean zero. It means the true rate is below about a quarter of a percent at ninety-five percent confidence. That\'s the claim, and we\'re not going to round it up to safe.' },
  { start: 209, end: 241, name: 'Our mistake',     title: 'One of these\nwas our mistake.',
    transcript: 'We built an ambiguous tier to catch the kernel deciding when it shouldn\'t. It caught us instead. A late period, no test, an isotretinoin order. We labelled it escalate. Jev said pregnancy is not ruled out, a hundred and fifteen times out of a hundred and twenty. Jev was right. The other case is real. Details unknown, amoxicillin ordered. Jev hedged eighty-six times, and decided thirty-four. The stability gate is not an answerability check.' },
  { start: 241, end: 255, name: 'What it enabled', title: 'None of this\nworks on prose.',
    transcript: 'None of this works on prose. It works because Jev hands you a probability. Something you can measure, gate on, replay, and write a spec against. What we learned building it is below.' },
];
export const DURATION = CHAPTERS.at(-1).end;

export function chapterAt(t) { const i = CHAPTERS.findIndex(c => t < c.end); return i === -1 ? CHAPTERS.length - 1 : i; }

// Stage 1440 x 720. Ring on the right so the headline owns the left.
export const AGENTS = ['rx1', 'rx2', 'rx3', 'rx4', 'rx5'];
export const RING = { cx: 880, cy: 390, r: 200 };
export function agentPos(i, r = RING.r) {
  const a = -Math.PI / 2 + i * (2 * Math.PI / 5);
  return { x: RING.cx + r * Math.cos(a), y: RING.cy + r * Math.sin(a), a };
}

const WIDE = { x: 0, y: 0, w: 1440 };
const RINGV = { x: 440, y: 110, w: 880 };
const NUM = { x: 700, y: 300, w: 360 };
const lerpCam = (a, b, k) => ({ x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k, w: a.w + (b.w - a.w) * k });
// Camera moves on a spring too, so pushes and pulls have weight.
const camSpring = (a, b, tl, at, dur) => lerpCam(a, b, clamp01(spring(Math.max(0, tl - at) / dur, { omega: 6, zeta: 0.9 })));

export function evaluate(input, D) {
  if (!Number.isFinite(input)) throw new TypeError('time must be finite');
  const t = Math.max(0, Math.min(DURATION, input));
  const ci = chapterAt(t), ch = CHAPTERS[ci];
  const tl = t - ch.start, len = ch.end - ch.start, local = tl / len;

  const M = D.moments || {};
  const clean = M.clean_decision || { votes: [] };
  const declined = M.declined_under_chaos || { votes: [], oracle_failures: [] };
  const degraded = M.degraded_but_decided || { surviving_votes: [], lost_agents: [] };

  const st = {
    t, ci, chapter: ch, tl, local, cam: WIDE, mode: 'title',
    votes: [], lost: [], retried: [], verdict: null, escalated: false,
    ringReveal: 0, nodeSpring: () => 1, packets: [], spokes: 0,
    // headline: each line springs in on a stagger, holds, then leaves
    titleLine: i => clamp01(spring(tl - 0.15 - i * 0.14, { omega: 9, zeta: 0.62 })),
    titleHold: 1 - easeInOut((tl - (len - 1.6)) / 1.2),
    titleYield: 0,
  };

  switch (ci) {
    case 0: st.mode = 'title'; break;

    case 1: { // the number counts up with a spring, then the ring assembles around it
      st.mode = 'number';
      const v = (clean.votes || [])[0];
      st.bigNumber = { value: v ? v.p : 0.98, label: 'controlled substance · oxycodone' };
      st.numberK = clamp01(spring(tl - 1.2, { omega: 5, zeta: 0.7 }));
      st.numberScale = 0.86 + 0.14 * clamp01(spring(tl - 1.2, { omega: 10, zeta: 0.45 }));
      st.cam = camSpring(NUM, RINGV, tl, 11, 3.2);
      st.ringReveal = clamp01((tl - 11.5) / 4);
      st.nodeSpring = i => clamp01(spring(tl - 11.8 - i * 0.28, { omega: 10, zeta: 0.5 }));
      break;
    }

    case 2: { // what we built. Beats are pinned to the spoken sentences (captions.json, chapter 2):
      //  3.2 TLA+   10.1 AsyncAPI   14.1 Rust + Jev   19.8 record   21.2 fan out   24.5 back   26.7 gate/quorum/decide   29.2 trace
      st.mode = 'arch';
      const at = [3.4, 10.3, 14.3];
      st.blocks = ['TLA+ spec', 'AsyncAPI contract', 'Rust kernel'].map((label, i) => ({ label, k: clamp01(spring(tl - at[i], { omega: 7, zeta: 0.55 })) }));
      st.arrows = [0, 1].map(i => clamp01((tl - at[i + 1] + 0.6) / 0.6));
      st.jevK = clamp01(spring(tl - 16.2, { omega: 7, zeta: 0.5 }));
      st.recordX = easeOutExpo((tl - 19.9) / 1.2);
      st.fan = clamp01((tl - 21.4) / 1.4);
      st.ret = [0, 1, 2, 3, 4].map(i => clamp01((tl - 24.6 - i * 0.3) / 1.1));
      st.retP = (clean.votes || []).map(v => v.p);
      st.gateK = clamp01(spring(tl - 26.9, { omega: 9, zeta: 0.6 }));
      st.decideK = clamp01(spring(tl - 28.1, { omega: 9, zeta: 0.5 }));
      st.traceK = clamp01((tl - 29.3) / 1.0);
      st.titleHold = 1 - easeInOut((tl - 2.6) / 0.8);
      st.cam = WIDE;
      break;
    }

    case 3: { // five voices: nodes already there; votes travel to the centre as packets
      st.mode = 'ring'; st.ringReveal = 1;
      st.votes = clean.votes || [];
      st.packets = st.votes.map((v, i) => ({ agent: v.agent, p: clamp01((tl - 8.5 - i * 0.9) / 1.1), landed: tl > 9.6 + i * 0.9 }));
      st.spokes = 1;
      st.verdict = tl > 12.6 ? (clean.verdict || 'Yes') : null;
      st.verdictK = clamp01(spring(tl - 12.6, { omega: 11, zeta: 0.42 }));
      st.cam = camSpring(RINGV, WIDE, tl, 16.5, 3);
      st.showIds = tl > 17.5; st.idsK = i => clamp01(spring(tl - 17.8 - i * 0.22, { omega: 9, zeta: 0.6 }));
      break;
    }

    case 4: { // the floor: votes rain in under gravity and settle on the floor line
      st.mode = 'gate';
      st.titleHold = 1 - easeInOut((tl - 4.2) / 1.0);
      st.rainStart = 5.0; st.rainSpread = 9.0;
      st.floorK = clamp01((tl - 9.5) / 1.4);
      st.cam = camSpring(WIDE, { x: 200, y: 330, w: 1040 }, tl, 18.5, 3);
      st.gapK = clamp01(spring(tl - 20.5, { omega: 8, zeta: 0.7 }));
      break;
    }

    case 5: { // chaos: the record card is attacked; the ring is jostled
      st.mode = 'chaos';
      st.titleHold = 1 - easeInOut((tl - 5.5) / 1.0);
      st.ringReveal = 1;
      // stay wide while the record is attacked (card on the left), push in once agents start taking hits
      st.cam = camSpring(WIDE, { x: 300, y: 60, w: 1100 }, tl, 12, 3.5);
      st.events = [
        { at: 7.0,  agent: 0, kind: 'inject' },
        { at: 9.4,  agent: 3, kind: 'crash' },
        { at: 11.6, agent: 1, kind: 'ratelimit' },
        { at: 13.8, agent: 4, kind: 'truncate' },
        { at: 16.0, agent: 2, kind: 'inject' },
      ];
      st.hits = st.events.filter(e => tl >= e.at);
      st.jostle = i => {
        let dx = 0, dy = 0;
        for (const e of st.hits) {
          const dt = tl - e.at, amp = 26 * Math.exp(-dt * 2.2) * (e.agent === i ? 1 : 0.35);
          dx += amp * Math.sin(dt * 28 + i); dy += amp * Math.cos(dt * 31 + i * 1.7);
        }
        const tex = Math.min(1, st.hits.length / 3) * 3;
        return { dx: dx + tex * noise1(tl * 3 + i * 7, 1), dy: dy + tex * noise1(tl * 3 + i * 11, 2) };
      };
      st.cardK = clamp01(spring(tl - 0.8, { omega: 7, zeta: 0.6 }));
      break;
    }

    case 6: { // it declined: the real round, packets arrive, one is refused at the floor
      st.mode = 'ring'; st.ringReveal = 1;
      const everVoted = new Set((declined.votes || []).map(v => v.agent));
      const fails = (declined.oracle_failures || []).map(f => f[0]);
      st.lost = tl > 1 ? [...new Set(fails.filter(a => !everVoted.has(a)))] : [];
      st.retried = tl > 1 ? [...new Set(fails.filter(a => everVoted.has(a)))] : [];
      const order = ['rx4', 'rx5', 'rx3'], arrive = [4.2, 6.0, 9.2];
      st.votes = (declined.votes || []).filter(v => tl >= arrive[order.indexOf(v.agent)] - 1.1);
      st.packets = st.votes.map(v => { const i = order.indexOf(v.agent); return { agent: v.agent, p: clamp01((tl - (arrive[i] - 1.1)) / 1.1), landed: tl >= arrive[i], refused: v.stable === false }; });
      st.spokes = 1;
      st.calloutK = clamp01(spring(tl - 10.2, { omega: 8, zeta: 0.7 }));
      st.escalated = tl > 13.2; st.verdict = st.escalated ? 'ESCALATE' : null;
      st.verdictK = clamp01(spring(tl - 13.2, { omega: 10, zeta: 0.45 }));
      st.cam = tl < 16 ? RINGV : camSpring(RINGV, WIDE, tl, 16, 3);
      st.replay = declined.replay;
      break;
    }

    case 7: { // degrade: the 71% bar fills with weight, then the after-state
      st.mode = 'degrade';
      st.titleHold = 1 - easeInOut((tl - 5.0) / 1.0);
      st.beforeK = clamp01(spring(tl - 5.5, { omega: 6, zeta: 0.8 }));
      st.barK = clamp01(spring(tl - 6.2, { omega: 4.5, zeta: 0.7 }));
      st.afterK = clamp01(spring(tl - 12.5, { omega: 6, zeta: 0.8 }));
      st.votes = degraded.surviving_votes || [];
      st.lost = (degraded.lost_agents || []).map(f => f[0]);
      st.exK = i => clamp01(spring(tl - 13.5 - i * 0.3, { omega: 9, zeta: 0.6 }));
      st.cam = WIDE;
      break;
    }

    case 8: { // zero: rows slam in; the zeros punch with overshoot; camera pushes in
      st.mode = 'table';
      st.titleHold = 1 - easeInOut((tl - 2.5) / 0.8);
      st.rowK = i => clamp01(spring(tl - 3.0 - i * 0.8, { omega: 9, zeta: 0.55 }));
      st.zeroK = clamp01(spring(tl - 8.0, { omega: 8, zeta: 0.32 }));
      const ZOOM = { x: 700, y: 150, w: 560 };
      st.cam = tl < 8 ? WIDE : tl < 15 ? camSpring(WIDE, ZOOM, tl, 8, 2.6) : camSpring(ZOOM, WIDE, tl, 15, 2.6);
      st.boundK = clamp01(spring(tl - 16.5, { omega: 7, zeta: 0.7 }));
      break;
    }

    case 9: { // our mistake: left column lands, camera pans right for the second half
      st.mode = 'honest';
      st.titleHold = 1 - easeInOut((tl - 2.4) / 0.8);
      st.leftK = i => clamp01(spring(tl - 2.8 - i * 0.22, { omega: 8, zeta: 0.6 }));
      st.rightK = i => clamp01(spring(tl - 17.0 - i * 0.22, { omega: 8, zeta: 0.6 }));
      st.cam = tl < 16 ? WIDE : camSpring(WIDE, { x: 640, y: 40, w: 800 }, tl, 16, 3);
      break;
    }

    case 10: st.mode = 'title'; st.cam = WIDE; break;
  }
  st.titleYield = clamp01((1440 - st.cam.w - 560) / 200);
  return st;
}

export function captionAt(t) {
  const ch = CHAPTERS[chapterAt(t)];
  const s = ch.transcript.match(/[^.!?]+[.!?]+|[^.!?]+$/g).map(x => x.trim());
  const total = s.reduce((n, x) => n + x.length, 0), p = (t - ch.start) / (ch.end - ch.start);
  let sum = 0; for (const x of s) { sum += x.length; if (p < sum / total) return x; } return s.at(-1);
}
export function formatTime(t) { const s = Math.floor(Math.max(0, t)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }
