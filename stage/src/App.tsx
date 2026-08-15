/**
 * Stage — **表現のみ**。ビジネスロジックを持たない。
 *
 * 判定基準: ストアから読める値は、すべて Core が `stage.*` で配信したもの。
 * Stage が自分で計算して状態を作っていたら、ロジックが漏れている。
 * → docs/architecture/ui.md §2
 */

import { useCallback, useState } from "react";

import { CharacterCanvas, type CharacterStatus } from "./character/CharacterCanvas";
import { useHoverState } from "./platform/useStageShell";

export function App() {
  const hover = useHoverState();
  const [status, setStatus] = useState<CharacterStatus>({ kind: null, fallbackReason: null });
  const onStatus = useCallback((next: CharacterStatus) => setStatus(next), []);

  return (
    <div className={hover === "inside" ? "stage stage--hover" : "stage"}>
      <CharacterCanvas onStatus={onStatus} />
      {/* **黙って劣化しない。** 本番の VRM ではなくプレースホルダで動いていることを見せる。 */}
      {status.fallbackReason && <p className="notice">{status.fallbackReason}</p>}
    </div>
  );
}
