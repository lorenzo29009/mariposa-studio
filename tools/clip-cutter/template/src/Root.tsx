import { Composition } from "remotion";
import { SegmentVideo } from "./Composition";
import { META, SEGMENTS } from "./segments";
import { SEG_SRTS } from "./srts";

// One composition per SEGMENT (id = segment key: "H1".."H5", "BODY", "CTA1", ...).
// Rendered once each; concat_combos.py assembles the hook x CTA matrix.
//
// srts.ts is written by build.py (never by a glob over segsrt/, which used to
// re-embed removed hooks). With the `ass` caption backend it is written EMPTY:
// captions are then burned by ffmpeg onto the finished 1080p segment, so a caption
// fix never re-enters Remotion. With the `remotion` backend it carries the cues and
// the composition draws them, pixel-identical to the pre-refactor output.
export const RemotionRoot: React.FC = () => {
  return (
    <>
      {SEGMENTS.map((seg) => (
        <Composition
          key={seg.key}
          id={seg.key}
          component={SegmentVideo as never}
          durationInFrames={seg.totalFrames}
          fps={META.fps}
          width={META.width}
          height={META.height}
          defaultProps={{ clips: seg.clips, srt: SEG_SRTS[seg.key] ?? "" }}
        />
      ))}
    </>
  );
};
