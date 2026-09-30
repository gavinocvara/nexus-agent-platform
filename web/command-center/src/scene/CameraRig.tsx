import { CameraControls, CameraControlsImpl } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import { type PerspectiveCamera, Vector3 } from "three";

import { STOPS, SYSTEMS, systemPosition } from "../director/systems";
import { useStore } from "../state/store";

const { ACTION } = CameraControlsImpl;
const UP = new Vector3(0, 1, 0);

export interface Pose {
  position: Vector3;
  target: Vector3;
  /** Horizontal framing shift as a fraction of the viewport width (+ moves the subject left). */
  shift: number;
}

export function poseFor(stop: number, aspect: number): Pose {
  const narrow = aspect < 1.1;
  const spread = narrow ? 1.55 : 1;
  const entry = STOPS[stop] ?? STOPS[0]!;
  if (entry.system === null) {
    return {
      position: new Vector3(0, 8.4 * spread, 19.5 * spread),
      target: new Vector3(0, -0.6, 0),
      shift: 0,
    };
  }
  if (entry.system === "nexus") {
    return {
      position: new Vector3(4.6, 1.7, 5.6).multiplyScalar(spread),
      target: new Vector3(0, 0.1, 0),
      shift: narrow ? 0 : 0.13,
    };
  }
  const p = new Vector3(...systemPosition(entry.system));
  const outward = p.clone().setY(0).normalize();
  const tangent = new Vector3().crossVectors(UP, outward).normalize();
  const size = SYSTEMS[entry.system].size;
  const reach = (5.0 + size * 1.6) * spread;
  return {
    position: p
      .clone()
      .addScaledVector(outward, reach)
      .addScaledVector(tangent, -2.6 * spread)
      .addScaledVector(UP, 1.35),
    target: p.clone().addScaledVector(outward, -0.55),
    shift: narrow ? 0 : 0.02,
  };
}

const STEP_THRESHOLD = 55;
const STEP_COOLDOWN_MS = 720;
const IDLE_DRIFT_AFTER_MS = 14_000;

export function CameraRig({ reducedMotion }: { reducedMotion: boolean }) {
  const controls = useRef<CameraControlsImpl>(null);
  const stop = useStore((state) => state.stop);
  const gl = useThree((state) => state.gl);
  const size = useThree((state) => state.size);
  const camera = useThree((state) => state.camera) as PerspectiveCamera;
  const shift = useRef(0);
  const shiftTarget = useRef(0);
  const lastInput = useRef(performance.now());

  // Move to the stop from wherever the user has orbited to: the rail never fights the hand.
  useEffect(() => {
    const cc = controls.current;
    if (!cc) return;
    const pose = poseFor(stop, size.width / Math.max(1, size.height));
    shiftTarget.current = pose.shift;
    void cc.setLookAt(
      pose.position.x,
      pose.position.y,
      pose.position.z,
      pose.target.x,
      pose.target.y,
      pose.target.z,
      !reducedMotion,
    );
    lastInput.current = performance.now();
  }, [stop, size.width, size.height, reducedMotion]);

  // Wheel and trackpad scroll step through the rail; Ctrl/Cmd (and pinch) zoom.
  useEffect(() => {
    const element = gl.domElement;
    let accumulated = 0;
    let lastStep = 0;
    let resetTimer: number | undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      lastInput.current = performance.now();
      const cc = controls.current;
      if (event.ctrlKey || event.metaKey) {
        if (cc) void cc.dolly(-event.deltaY * 0.006 * Math.max(2, cc.distance * 0.25), true);
        return;
      }
      accumulated += event.deltaY;
      window.clearTimeout(resetTimer);
      resetTimer = window.setTimeout(() => {
        accumulated = 0;
      }, 180);
      const nowMs = performance.now();
      if (Math.abs(accumulated) >= STEP_THRESHOLD && nowMs - lastStep > STEP_COOLDOWN_MS) {
        useStore.getState().step(accumulated > 0 ? 1 : -1);
        accumulated = 0;
        lastStep = nowMs;
      }
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      element.removeEventListener("wheel", onWheel);
      window.clearTimeout(resetTimer);
    };
  }, [gl]);

  useFrame((_, delta) => {
    // Eased framing shift via a view offset, so the orbit pivot stays on the subject.
    shift.current += (shiftTarget.current - shift.current) * Math.min(1, delta * (reducedMotion ? 60 : 2.4));
    const offset = shift.current * size.width;
    if (Math.abs(offset) > 0.5) {
      camera.setViewOffset(size.width, size.height, offset, 0, size.width, size.height);
    } else if (camera.view?.enabled) {
      camera.clearViewOffset();
    }
    const cc = controls.current;
    if (!cc || reducedMotion) return;
    const idle = performance.now() - lastInput.current > IDLE_DRIFT_AFTER_MS;
    if (idle && useStore.getState().stop === 0 && useStore.getState().view === "space") {
      void cc.rotate(delta * 0.028, 0, false);
    }
  });

  return (
    <CameraControls
      ref={controls}
      makeDefault
      minDistance={2.4}
      maxDistance={46}
      smoothTime={0.85}
      draggingSmoothTime={0.14}
      mouseButtons={{ left: ACTION.ROTATE, middle: ACTION.DOLLY, right: ACTION.TRUCK, wheel: ACTION.NONE }}
      touches={{ one: ACTION.TOUCH_ROTATE, two: ACTION.TOUCH_DOLLY_TRUCK, three: ACTION.TOUCH_TRUCK }}
      onControlStart={() => {
        lastInput.current = performance.now();
      }}
    />
  );
}
