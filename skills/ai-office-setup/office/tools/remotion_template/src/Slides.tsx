import React from "react";
import {AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";

export type Scene = {heading: string; body?: string; emoji?: string};
export type SlidesProps = {
  title: string; subtitle?: string; scenes: Scene[]; secondsPerScene: number;
  bg?: string; accent?: string; width?: number; height?: number;
};

const font = '"Apple SD Gothic Neo", "Pretendard", "Noto Sans KR", sans-serif';

const Card: React.FC<{heading: string; body?: string; emoji?: string; accent: string; big?: boolean}> = ({heading, body, emoji, accent, big}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const s = spring({frame, fps, config: {damping: 14}});
  const fade = interpolate(frame, [0, 10], [0, 1], {extrapolateRight: "clamp"});
  const scale = width / 1920;
  return (
    <AbsoluteFill style={{alignItems: "center", justifyContent: "center", fontFamily: font, opacity: fade}}>
      <div style={{transform: `translateY(${(1 - s) * 60}px)`, textAlign: "center", maxWidth: "82%"}}>
        {emoji ? <div style={{fontSize: 120 * scale, marginBottom: 20 * scale}}>{emoji}</div> : null}
        <div style={{fontSize: (big ? 110 : 84) * scale, fontWeight: 900, color: "#2d2a26", lineHeight: 1.25}}>{heading}</div>
        <div style={{height: 10 * scale, width: 160 * scale * s, background: accent, margin: `${28 * scale}px auto`, borderRadius: 8}} />
        {body ? <div style={{fontSize: 48 * scale, color: "#4a4038", lineHeight: 1.6, whiteSpace: "pre-wrap"}}>{body}</div> : null}
      </div>
    </AbsoluteFill>
  );
};

export const Slides: React.FC<SlidesProps> = ({title, subtitle, scenes, secondsPerScene, bg = "#f4efe7", accent = "#7c5cff"}) => {
  const {fps} = useVideoConfig();
  const len = Math.round(fps * secondsPerScene);
  return (
    <AbsoluteFill style={{background: bg}}>
      <Sequence durationInFrames={len}><Card heading={title} body={subtitle} accent={accent} big /></Sequence>
      {scenes.map((sc, i) => (
        <Sequence key={i} from={len * (i + 1)} durationInFrames={len}><Card {...sc} accent={accent} /></Sequence>
      ))}
    </AbsoluteFill>
  );
};
