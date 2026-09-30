import { Component, type ReactNode, Suspense, lazy, useEffect } from "react";

import { api, openStream } from "./data/api";
import { STOPS } from "./director/systems";
import { Footer, Menu, TopBar } from "./hud/Chrome";
import { OwnerOverlay } from "./hud/OwnerOverlay";
import { ReplayDeck } from "./hud/ReplayDeck";
import { ActivityView, DataView } from "./hud/Sheets";
import { SystemDisplay } from "./hud/SystemDisplay";
import { type Quality, useStore } from "./state/store";

const Scene = lazy(() => import("./scene/Scene"));

function detectWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

function initialQuality(params: URLSearchParams): Quality {
  const requested = params.get("quality");
  if (requested === "high" || requested === "medium" || requested === "low") return requested;
  const cores = navigator.hardwareConcurrency ?? 4;
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8;
  const small = window.matchMedia("(max-width: 820px)").matches;
  if (cores <= 4 || memory <= 4 || small) return "medium";
  return "high";
}

class SceneBoundary extends Component<{ children: ReactNode; onError: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function useBootstrap() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const store = useStore.getState();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches || params.has("still");
    const webgl = detectWebGL() && params.get("webgl") !== "0";
    store.setEnvironment({ reducedMotion, webgl });
    store.setQuality(initialQuality(params));
    const view = params.get("view");
    if (!webgl) store.setView("data");
    else if (view === "data" || view === "activity" || view === "space") store.setView(view);
    const stop = Number(params.get("stop"));
    if (Number.isInteger(stop) && stop > 0) store.goTo(stop);
    if (params.get("mode") === "replay") {
      store.setMode("replay");
      const name = params.get("episode");
      if (name) {
        const at = Number(params.get("t") ?? "0");
        const load = (attempt: number) =>
          api
            .replayEpisode(name)
            .then((episode) => {
              useStore.getState().loadEpisode(episode);
              if (at > 0) useStore.getState().seek(at);
              if (params.has("paused")) useStore.getState().playPause();
            })
            .catch(() => {
              if (attempt < 20) window.setTimeout(() => load(attempt + 1), 500);
            });
        load(0);
      }
    }
    api.snapshot().then(store.setSnapshot).catch(() => undefined);
    return openStream({
      onSnapshot: (snapshot) => useStore.getState().setSnapshot(snapshot),
      onSignal: (signal) => useStore.getState().receiveSignal(signal),
      onState: (state) => useStore.getState().setConnection(state),
    });
  }, []);
}

function useKeyboard() {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA")) return;
      const store = useStore.getState();
      if (event.key === "Escape") {
        if (store.view !== "space" && store.webgl) store.setView("space");
        else store.goTo(0);
        return;
      }
      if (store.view !== "space") return;
      if (target?.closest(".nx-menu")) return; // the menu handles its own arrows
      if (["ArrowRight", "ArrowDown", "PageDown", "j"].includes(event.key)) {
        event.preventDefault();
        store.step(1);
      } else if (["ArrowLeft", "ArrowUp", "PageUp", "k"].includes(event.key)) {
        event.preventDefault();
        store.step(-1);
      } else if (event.key === "Home") {
        store.goTo(0);
      } else if (/^[1-7]$/.test(event.key)) {
        store.goTo(Number(event.key) - 1);
      } else if (event.key === " " && store.mode === "replay" && store.replay.episode) {
        event.preventDefault();
        store.playPause();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

function Announcer() {
  const announcement = useStore((state) => state.announcement);
  const stop = useStore((state) => state.stop);
  return (
    <div className="nx-live" aria-live="polite" role="status">
      {announcement} Focus: {STOPS[stop]?.label}.
    </div>
  );
}

export default function App() {
  useBootstrap();
  useKeyboard();
  const mode = useStore((state) => state.mode);
  const view = useStore((state) => state.view);
  const webgl = useStore((state) => state.webgl);
  const setEnvironment = useStore((state) => state.setEnvironment);
  const reducedMotion = useStore((state) => state.reducedMotion);
  const overlay = useStore((state) => {
    if (state.view !== "space") return false;
    if (state.mode === "replay") {
      const approval = state.replay.episode?.cycle.approval;
      return Boolean(approval && state.replay.ownerVisible && state.dismissedRequest !== approval.request_id);
    }
    const pending = state.snapshot?.pending_approval;
    return Boolean(pending && state.dismissedRequest !== pending.request_id);
  });
  return (
    <div className="nx-app" data-mode={mode} data-view={view} data-overlay={overlay}>
      <a className="nx-skip" href="#nx-main">
        Skip to system information
      </a>
      <div className="nx-stage" aria-hidden="true">
        {webgl ? (
          <SceneBoundary
            onError={() => {
              setEnvironment({ reducedMotion, webgl: false });
              useStore.getState().setView("data");
            }}
          >
            <Suspense fallback={<div className="nx-fallback">INITIALIZING SCENE</div>}>
              <Scene />
            </Suspense>
          </SceneBoundary>
        ) : null}
      </div>
      <div className="nx-edge" aria-hidden="true" />
      {mode === "replay" ? (
        <div className="nx-replay-band" aria-hidden="true">
          REPLAY · EVALUATION CATALOG · NOT LIVE
        </div>
      ) : null}
      <div className="nx-hud">
        <TopBar />
        <Menu />
        {mode === "replay" ? <ReplayDeck /> : null}
        <SystemDisplay />
        <Footer />
      </div>
      <OwnerOverlay />
      {view === "data" ? <DataView fallback={!webgl} /> : null}
      {view === "activity" ? <ActivityView /> : null}
      <Announcer />
    </div>
  );
}
