import React from "react";
import {Composition} from "remotion";
import {Slides, SlidesProps} from "./Slides";

// 장면 수 × 장면 길이(초)로 영상 길이를 정해요. props.json 으로 내용을 바꿔요.
const defaults: SlidesProps = {
  title: "제목",
  subtitle: "부제목",
  scenes: [{heading: "첫 장면", body: "여기에 설명을 적어요"}],
  secondsPerScene: 4,
  bg: "#f4efe7",
  accent: "#7c5cff",
  width: 1920,
  height: 1080,
};

export const Root: React.FC = () => (
  <Composition
    id="Slides"
    component={Slides}
    defaultProps={defaults}
    fps={30}
    width={1920}
    height={1080}
    durationInFrames={30 * 4 * 2}
    calculateMetadata={({props}) => ({
      durationInFrames: Math.round(30 * props.secondsPerScene * (props.scenes.length + 1)),
      width: props.width || 1920,
      height: props.height || 1080,
    })}
  />
);
