import { AbsoluteFill, OffthreadVideo, Series, Sequence, staticFile, useVideoConfig } from "remotion";
import { loadFont } from "@remotion/google-fonts/Inter";
import { parseSrt } from "@remotion/captions";
import { captionStyle } from "./caption-style";

// only 800 is used by captionStyle; loading 700 was a wasted font fetch per bundle
const { fontFamily } = loadFont("normal", { weights: ["800"] });

export type Clip = { src: string; trimBefore: number; trimAfter: number };

// One SEGMENT (a hook, the body, or a CTA): its trimmed clips + its own captions,
// timed from the segment start. Rendered once; combos concat these. Sources display
// 9:16 via rotation metadata, so objectFit:"cover" fills with no crop.
export const SegmentVideo: React.FC<{ clips: readonly Clip[]; srt: string }> = ({
  clips,
  srt,
}) => {
  const { fps } = useVideoConfig();
  // Empty srt => clean render (the `ass` backend burns captions downstream with
  // ffmpeg). Non-empty => Remotion draws them, as it always did.
  const { captions } = parseSrt({ input: srt || "" });
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <Series>
        {clips.map((clip, i) => (
          <Series.Sequence key={i} durationInFrames={clip.trimAfter - clip.trimBefore}>
            <OffthreadVideo
              src={staticFile(clip.src)}
              trimBefore={clip.trimBefore}
              trimAfter={clip.trimAfter}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          </Series.Sequence>
        ))}
      </Series>
      {captions.map((c, i) => {
        const from = Math.round((c.startMs / 1000) * fps);
        const to = Math.round((c.endMs / 1000) * fps);
        return (
          <Sequence key={`c${i}`} from={from} durationInFrames={Math.max(1, to - from)}>
            <AbsoluteFill>
              <div style={captionStyle(fontFamily)}>{c.text}</div>
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
