import { useEffect, useState } from "react";

import { api } from "../data/api";
import { REPLAY_STEP_SECONDS } from "../director/director";
import { buildTimeline } from "../director/timeline";
import { shortSha, upper } from "../lib/format";
import { replayTime, useStore } from "../state/store";

function useReplayClock(active: boolean): number {
  const [, force] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => force((value) => value + 1), 200);
    return () => window.clearInterval(timer);
  }, [active]);
  return replayTime(useStore.getState().replay);
}

export function ReplayDeck() {
  const replay = useStore((state) => state.replay);
  const setCatalog = useStore((state) => state.setReplayCatalog);
  const loadEpisode = useStore((state) => state.loadEpisode);
  const playPause = useStore((state) => state.playPause);
  const restart = useStore((state) => state.restart);
  const setSpeed = useStore((state) => state.setSpeed);
  const seek = useStore((state) => state.seek);
  const goTo = useStore((state) => state.goTo);
  const [error, setError] = useState<string | null>(null);
  const time = useReplayClock(replay.playing);

  useEffect(() => {
    if (replay.catalog?.state === "ok") return;
    const controller = new AbortController();
    const load = () =>
      api
        .replayCatalog(controller.signal)
        .then((catalog) => {
          setCatalog(catalog);
          if (catalog.state === "preparing") window.setTimeout(load, 1500);
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) setError(String(reason));
        });
    load();
    return () => controller.abort();
  }, [replay.catalog?.state, setCatalog]);

  const choose = (name: string) => {
    setError(null);
    api
      .replayEpisode(name)
      .then((episode) => {
        loadEpisode(episode);
        goTo(0);
      })
      .catch((reason: unknown) => setError(String(reason)));
  };

  const catalog = replay.catalog;
  const episode = replay.episode;
  const duration = episode
    ? buildTimeline(episode.cycle, { step: REPLAY_STEP_SECONDS, owner: episode.owner_step }).duration
    : 0;

  return (
    <section className="nx-deck" aria-label="Replay controls">
      <div className="nx-deck__row">
        <label className="nx-sr" htmlFor="nx-episode">
          Replay episode
        </label>
        <select
          id="nx-episode"
          value={episode?.name ?? ""}
          onChange={(event) => choose(event.target.value)}
          disabled={!catalog || catalog.state !== "ok"}
        >
          <option value="" disabled>
            {catalog?.state === "ok" ? "Choose a recorded episode…" : catalog ? `Replay ${catalog.state}…` : "Loading…"}
          </option>
          {catalog?.episodes.map((item) => (
            <option key={item.name} value={item.name}>
              {item.title} — {upper(item.decision)}
            </option>
          ))}
        </select>
        <button className="nx-deck__btn" onClick={restart} disabled={!episode} aria-label="Restart episode">
          ⟲
        </button>
        <button
          className="nx-deck__btn"
          onClick={playPause}
          disabled={!episode}
          aria-label={replay.playing ? "Pause" : "Play"}
          aria-pressed={replay.playing}
        >
          {replay.playing ? "❚❚" : "▶"}
        </button>
        {[1, 2].map((speed) => (
          <button
            key={speed}
            className="nx-deck__btn"
            aria-pressed={replay.speed === speed}
            onClick={() => setSpeed(speed)}
            disabled={!episode}
          >
            {speed}×
          </button>
        ))}
      </div>
      {episode ? (
        <>
          <input
            className="nx-scrub"
            type="range"
            min={0}
            max={duration}
            step={0.05}
            value={Math.min(time, duration)}
            onChange={(event) => seek(Number(event.target.value))}
            aria-label="Replay position"
          />
          <p className="nx-deck__synopsis">{episode.synopsis}</p>
          <p className="nx-deck__prov">
            {episode.provenance} {episode.tempo_note} Record <span className="nx-mono">{shortSha(episode.record_sha256, 16)}</span>
          </p>
        </>
      ) : (
        <p className="nx-deck__synopsis">
          {catalog?.detail ??
            "Recorded episodes from the resident engineer's evaluation catalog, re-executed at server start without a model or network."}
        </p>
      )}
      {error ? (
        <p className="nx-deck__prov" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
