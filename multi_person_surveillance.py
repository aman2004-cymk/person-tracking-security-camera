"""Operator-supervised person tracking with optional local face matching.

Uses YOLO tracking for stable on-screen track IDs and the local LBPH enrollment
database for advisory identification. A single visible track is followed
automatically; the operator selects a target when multiple people are visible.

Requirements:
    py -m pip install ultralytics opencv-contrib-python numpy
"""
import argparse
import csv
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from hardware_bridge import Esp32Bridge, SafeEsp32Controller
from face_attendance import (
    FACE_SCALE, MATCH_THRESHOLD, MIN_NEIGHBORS, face_detector, load_model,
    normalise_face,
)

PROJECT_DIR = Path(__file__).resolve().parent
MODEL_PATH = PROJECT_DIR / "yolo11n.pt"
AUTHORIZATION_PATH = PROJECT_DIR / "authorized_people.csv"
ALERT_DIR = PROJECT_DIR / "alerts"
FOV_X_DEG, FOV_Y_DEG = 70.0, 45.0
PAN_MIN, PAN_MAX, TILT_MIN, TILT_MAX = -90.0, 90.0, -30.0, 60.0
GAIN, DEADZONE, MAX_RATE = 0.10, 14.0, 45.0
ALERT_CONFIRMATION_FRAMES = 5
ALERT_COOLDOWN_SECONDS = 30.0
ALERT_DEDUPLICATION_DISTANCE_PX = 180.0
TRACK_STALE_SECONDS = 1.2
FACE_RECOGNITION_EVERY = 6


def load_authorized_people() -> set[str]:
    """Read the explicit authorization list; enrollment alone is not authority."""
    if not AUTHORIZATION_PATH.exists():
        with AUTHORIZATION_PATH.open("w", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=["person_name", "active"]).writeheader()
        print(f"Created {AUTHORIZATION_PATH.name}. Add approved enrolled names before use.")
        return set()
    with AUTHORIZATION_PATH.open(newline="", encoding="utf-8") as file:
        return {
            row["person_name"].strip()
            for row in csv.DictReader(file)
            if row.get("person_name", "").strip() and row.get("active", "").strip().lower() in {"1", "true", "yes", "active"}
        }


def daily_alert_path(now: datetime) -> Path:
    return ALERT_DIR / f"alerts_{now:%Y-%m-%d}.csv"


def daily_incident_path(now: datetime) -> Path:
    return ALERT_DIR / f"incidents_{now:%Y-%m-%d}.csv"


def model_reference() -> str:
    """Prefer a project-local YOLO model, otherwise let Ultralytics fetch its official asset."""
    return str(MODEL_PATH) if MODEL_PATH.is_file() else MODEL_PATH.name


def record_alert(frame: np.ndarray, track_id: int, identity: str | None, status: str, confidence: float) -> None:
    """Write a tamper-evident event row, still image, and SHA-256 digest."""
    now = datetime.now()
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    image_path = ALERT_DIR / f"{stamp}_track{track_id}_{status.lower()}.jpg"
    if not cv2.imwrite(str(image_path), frame):
        raise OSError(f"Could not save alert evidence image: {image_path}")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    log_path = daily_alert_path(now)
    new_file = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "timestamp", "track_id", "identity", "status", "face_distance", "image_file", "sha256",
        ])
        if new_file:
            writer.writeheader()
        writer.writerow({
            "timestamp": now.isoformat(timespec="seconds"), "track_id": track_id,
            "identity": identity or "", "status": status,
            "face_distance": f"{confidence:.2f}" if confidence < float("inf") else "",
            "image_file": image_path.name, "sha256": digest,
        })
    print(f"ALERT: {status} | track {track_id} | {identity or 'unknown'}")


def record_incident(
    frame: np.ndarray, track_id: int, identity: str | None, status: str, confidence: float,
) -> Path:
    """Persist an operator-confirmed incident and return its evidence image path."""
    now = datetime.now()
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S_%f")
    image_path = ALERT_DIR / f"{stamp}_track{track_id}_operator_incident.jpg"
    if not cv2.imwrite(str(image_path), frame):
        raise OSError(f"Could not save incident evidence image: {image_path}")
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    log_path = daily_incident_path(now)
    new_file = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "timestamp", "action", "track_id", "identity", "status", "face_distance", "image_file", "sha256",
        ])
        if new_file:
            writer.writeheader()
        writer.writerow({
            "timestamp": now.isoformat(timespec="seconds"), "action": "OPERATOR_CONFIRMED_NOTIFY_SECURITY",
            "track_id": track_id, "identity": identity or "", "status": status,
            "face_distance": f"{confidence:.2f}" if confidence < float("inf") else "",
            "image_file": image_path.name, "sha256": digest,
        })
    print(f"OPERATOR-CONFIRMED SECURITY NOTIFICATION: track {track_id} | {identity or 'unknown'} | {status}")
    return image_path


def alert_is_duplicate(
    recent_events: list[dict], status: str, identity: str | None,
    box: tuple[int, int, int, int], now: float,
) -> bool:
    """Suppress tracker-ID churn without merging distinct nearby people.

    ByteTrack can assign a replacement ID after an occlusion or a weak webcam
    frame. Alerts therefore use a short-lived event identity: same status and
    recognised identity, or (for unknown people) a nearby bounding-box centre.
    """
    recent_events[:] = [event for event in recent_events if now - event["time"] < ALERT_COOLDOWN_SECONDS]
    centre_x, centre_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for event in recent_events:
        if event["status"] != status or event["identity"] != identity:
            continue
        distance = float(np.hypot(centre_x - event["centre_x"], centre_y - event["centre_y"]))
        if distance <= ALERT_DEDUPLICATION_DISTANCE_PX:
            return True
    return False


def remember_alert(recent_events: list[dict], status: str, identity: str | None,
                  box: tuple[int, int, int, int], now: float) -> None:
    recent_events.append({
        "status": status, "identity": identity, "time": now,
        "centre_x": (box[0] + box[2]) / 2, "centre_y": (box[1] + box[3]) / 2,
    })


def choose_track(current: int | None, active_ids: list[int], direction: int) -> int | None:
    if not active_ids:
        return None
    if current not in active_ids:
        return active_ids[0]
    return active_ids[(active_ids.index(current) + direction) % len(active_ids)]


def classify(identity: str | None, authorized: set[str]) -> str:
    if identity is None:
        return "UNREGISTERED"
    return "AUTHORIZED" if identity in authorized else "UNAUTHORIZED"


def virtual_view(frame: np.ndarray, pan: float, tilt: float) -> np.ndarray:
    height, width = frame.shape[:2]
    matrix = np.float32(((1.0, 0.0, round((-pan / FOV_X_DEG) * width)),
                         (0.0, 1.0, round((tilt / FOV_Y_DEG) * height))))
    shifted = cv2.warpAffine(frame, matrix, (width, height), borderValue=(17, 24, 39))
    cv2.drawMarker(shifted, (width // 2, height // 2), (0, 255, 255), cv2.MARKER_CROSS, 26, 2)
    cv2.putText(shifted, f"FOLLOW VIEW  pan {pan:+.1f}  tilt {tilt:+.1f}", (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
    return shifted


@dataclass
class VirtualPanTiltCamera:
    """Camera crop driven by the same pan/tilt state sent to the ESP32.

    A wide recorded scene is the virtual world.  YOLO receives only this moving
    crop, which makes replay a closed-loop test rather than a visual overlay.
    """
    scene_fov_x: float = 130.0
    scene_fov_y: float = 90.0

    def __post_init__(self) -> None:
        if self.scene_fov_x <= FOV_X_DEG or self.scene_fov_y <= FOV_Y_DEG:
            raise ValueError("Virtual-scene FOV must be wider than the camera FOV.")

    @property
    def pan_limits(self) -> tuple[float, float]:
        available = (self.scene_fov_x - FOV_X_DEG) / 2
        return max(PAN_MIN, -available), min(PAN_MAX, available)

    @property
    def tilt_limits(self) -> tuple[float, float]:
        available = (self.scene_fov_y - FOV_Y_DEG) / 2
        return max(TILT_MIN, -available), min(TILT_MAX, available)

    def bounds(self, frame: np.ndarray, pan: float, tilt: float) -> tuple[int, int, int, int]:
        height, width = frame.shape[:2]
        crop_width = max(2, round(width * FOV_X_DEG / self.scene_fov_x))
        crop_height = max(2, round(height * FOV_Y_DEG / self.scene_fov_y))
        limited_pan = float(np.clip(pan, *self.pan_limits))
        limited_tilt = float(np.clip(tilt, *self.tilt_limits))
        centre_x = width / 2 + limited_pan / self.scene_fov_x * width
        centre_y = height / 2 - limited_tilt / self.scene_fov_y * height
        left = int(np.clip(round(centre_x - crop_width / 2), 0, width - crop_width))
        top = int(np.clip(round(centre_y - crop_height / 2), 0, height - crop_height))
        return left, top, crop_width, crop_height

    def capture(self, frame: np.ndarray, pan: float, tilt: float) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        left, top, crop_width, crop_height = self.bounds(frame, pan, tilt)
        crop = frame[top:top + crop_height, left:left + crop_width]
        return cv2.resize(crop, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR), (left, top, crop_width, crop_height)

    def overview(self, frame: np.ndarray, bounds: tuple[int, int, int, int]) -> np.ndarray:
        left, top, crop_width, crop_height = bounds
        overview = frame.copy()
        cv2.rectangle(overview, (left, top), (left + crop_width, top + crop_height), (0, 255, 255), 3)
        cv2.putText(overview, "WIDE SCENE / VIRTUAL CAMERA CROP", (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
        return overview


def open_video_source(source: str | int) -> cv2.VideoCapture:
    """Accept webcam indices, local videos, or an OpenCV-supported stream URL."""
    source_value: str | int = int(source) if isinstance(source, str) and source.isdigit() else source
    camera = cv2.VideoCapture(source_value, cv2.CAP_DSHOW) if isinstance(source_value, int) else cv2.VideoCapture(source_value)
    if not camera.isOpened() and isinstance(source_value, int):
        camera.release()
        camera = cv2.VideoCapture(source_value)
    return camera


def run(
    source: str | int,
    hardware_backend: str = "real",
    esp32_port: str | None = None,
    closed_loop_replay: bool = False,
    scene_fov_x: float = 130.0,
    scene_fov_y: float = 90.0,
) -> None:
    if hardware_backend == "real" and not esp32_port:
        raise ValueError("--esp32-port is required with --hardware-backend real")
    authorized = load_authorized_people()
    try:
        recognizer, labels = load_model()
        detector = face_detector()
        recognition_ready = True
    except RuntimeError as error:
        recognizer, labels, detector, recognition_ready = None, {}, None, False
        print(f"Face recognition unavailable: {error}. Person tracking continues; all detected people are shown as unregistered.")

    model = YOLO(model_reference())
    hardware: SafeEsp32Controller | None = None
    camera = open_video_source(source)
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open video source {source!r}.")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    virtual_camera = VirtualPanTiltCamera(scene_fov_x, scene_fov_y) if closed_loop_replay else None

    tracks: dict[int, dict] = {}
    selected_id: int | None = None
    follow_enabled = False
    auto_following = False
    auto_follow_inhibited = False
    pan = tilt = 0.0
    pan_rate = tilt_rate = 0.0
    last_tick = time.monotonic()
    frame_number = 0
    recent_alert_events: list[dict] = []
    notification_message = ""
    notification_until = 0.0
    cv2.namedWindow("Multi-person Surveillance — Q to quit", cv2.WINDOW_NORMAL)
    try:
        if hardware_backend == "real":
            hardware = SafeEsp32Controller(esp32_port, bridge=Esp32Bridge(esp32_port))
            print(f"Hardware backend: real ESP32 on {esp32_port}")
        else:
            try:
                from simulated_hardware_bridge import SimulatedEsp32Bridge
            except ImportError as error:
                raise RuntimeError(
                    "The simulation transport is development-only and is not included in the "
                    "production package. Use --hardware-backend real --esp32-port COMx."
                ) from error

            hardware = SafeEsp32Controller("simulated", bridge=SimulatedEsp32Bridge())
            print("Hardware backend: simulated ESP32 (no motors or serial device required)")
        while True:
            ok, raw = camera.read()
            if not ok:
                break
            camera_frame, crop_bounds = virtual_camera.capture(raw, pan, tilt) if virtual_camera else (raw, None)
            now = time.monotonic()
            dt = min(0.2, max(0.0, now - last_tick))
            last_tick = now
            frame_number += 1
            result = model.track(camera_frame, classes=[0], conf=0.30, persist=True, tracker="bytetrack.yaml", verbose=False)[0]
            annotated = camera_frame.copy()
            active_ids = []
            if result.boxes is not None and result.boxes.id is not None:
                boxes = result.boxes.xyxy.cpu().tolist()
                ids = result.boxes.id.int().cpu().tolist()
                confidences = result.boxes.conf.cpu().tolist()
                for box, track_id, person_confidence in zip(boxes, ids, confidences):
                    x1, y1, x2, y2 = map(int, box)
                    active_ids.append(track_id)
                    record = tracks.setdefault(track_id, {
                        "identity": None, "distance": float("inf"), "alert_frames": 0, "alerted_at": 0.0,
                        "identity_checked": not recognition_ready, "recognition_cycles": 0,
                        "incident_reported": False,
                    })
                    record.update({"box": (x1, y1, x2, y2), "last_seen": now, "person_confidence": person_confidence})

            # Associate recognised faces with their containing YOLO person box.
            if recognition_ready and frame_number % FACE_RECOGNITION_EVERY == 0:
                gray = cv2.cvtColor(camera_frame, cv2.COLOR_BGR2GRAY)
                faces = detector.detectMultiScale(gray, scaleFactor=FACE_SCALE, minNeighbors=MIN_NEIGHBORS, minSize=(70, 70))
                for fx, fy, fw, fh in faces:
                    centre = (fx + fw / 2, fy + fh / 2)
                    candidates = [
                        (track_id, item) for track_id, item in tracks.items()
                        if "box" in item and item["box"][0] <= centre[0] <= item["box"][2] and item["box"][1] <= centre[1] <= item["box"][3]
                    ]
                    if not candidates:
                        continue
                    track_id, record = min(candidates, key=lambda pair: (pair[1]["box"][2] - pair[1]["box"][0]) * (pair[1]["box"][3] - pair[1]["box"][1]))
                    label, distance = recognizer.predict(normalise_face(gray, (fx, fy, fw, fh)))
                    record["identity"] = labels.get(label) if distance <= MATCH_THRESHOLD else None
                    record["distance"] = float(distance)
                    record["identity_checked"] = True
                # Give a new person two face-recognition passes before treating
                # a missing face match as unregistered (face may be turned away).
                for track_id in active_ids:
                    record = tracks[track_id]
                    record["recognition_cycles"] += 1
                    if record["recognition_cycles"] >= 2:
                        record["identity_checked"] = True

            for track_id in list(tracks):
                if now - tracks[track_id].get("last_seen", 0) > TRACK_STALE_SECONDS:
                    del tracks[track_id]
            active_ids = sorted(set(active_ids))
            if not active_ids:
                selected_id = None
                follow_enabled = False
                auto_following = False
                auto_follow_inhibited = False
            elif len(active_ids) == 1 and not auto_follow_inhibited:
                # A single visible person is unambiguous, so begin tracking
                # automatically.  Multiple people always require an operator
                # to explicitly select the intended subject.
                selected_id = active_ids[0]
                follow_enabled = True
                auto_following = True
            elif auto_following or auto_follow_inhibited:
                # The scene is no longer unambiguous; never silently carry an
                # automatically selected subject into a multi-person scene.
                selected_id = None
                follow_enabled = False
                auto_following = False
            elif selected_id not in active_ids:
                selected_id = None
                follow_enabled = False

            target = tracks.get(selected_id) if follow_enabled and selected_id is not None else None
            if target and "box" in target:
                x1, y1, x2, y2 = target["box"]
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                if virtual_camera:
                    error_x, error_y = cx - camera_frame.shape[1] / 2, -(cy - camera_frame.shape[0] / 2)
                else:
                    virtual_x = cx + (-pan / FOV_X_DEG) * camera_frame.shape[1]
                    virtual_y = cy + (tilt / FOV_Y_DEG) * camera_frame.shape[0]
                    error_x, error_y = virtual_x - camera_frame.shape[1] / 2, -(virtual_y - camera_frame.shape[0] / 2)
                pan_rate = 0.0 if abs(error_x) <= DEADZONE else float(np.clip(GAIN * error_x, -MAX_RATE, MAX_RATE))
                tilt_rate = 0.0 if abs(error_y) <= DEADZONE else float(np.clip(GAIN * error_y, -MAX_RATE, MAX_RATE))
                pan_limits = virtual_camera.pan_limits if virtual_camera else (PAN_MIN, PAN_MAX)
                tilt_limits = virtual_camera.tilt_limits if virtual_camera else (TILT_MIN, TILT_MAX)
                pan = float(np.clip(pan + pan_rate * dt, *pan_limits))
                tilt = float(np.clip(tilt + tilt_rate * dt, *tilt_limits))
            else:
                pan_rate = tilt_rate = 0.0

            hardware_alert = False
            for track_id in active_ids:
                record = tracks[track_id]
                status = classify(record["identity"], authorized)
                suspicious = status != "AUTHORIZED" and record["identity_checked"]
                hardware_alert = hardware_alert or suspicious
                record["alert_frames"] = record["alert_frames"] + 1 if suspicious else 0
                can_alert = not alert_is_duplicate(recent_alert_events, status, record["identity"], record["box"], now)
                if (suspicious and record["alert_frames"] >= ALERT_CONFIRMATION_FRAMES
                        and now - record["alerted_at"] >= ALERT_COOLDOWN_SECONDS and can_alert):
                    record_alert(camera_frame, track_id, record["identity"], status, record["distance"])
                    record["alerted_at"] = now
                    remember_alert(recent_alert_events, status, record["identity"], record["box"], now)
                x1, y1, x2, y2 = record["box"]
                colour = (40, 220, 40) if status == "AUTHORIZED" else (0, 0, 255) if status == "UNAUTHORIZED" else (0, 165, 255)
                chosen = (
                    "  AUTO-FOLLOW" if auto_following and track_id == selected_id
                    else "  FOLLOWING" if follow_enabled and track_id == selected_id
                    else "  SELECTED" if track_id == selected_id else ""
                )
                name = record["identity"] or "Unknown"
                if track_id == selected_id:
                    cv2.rectangle(annotated, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), (255, 255, 0), 4)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), colour, 3 if track_id == selected_id else 2)
                cv2.putText(annotated, f"#{track_id} {name} — {status}{chosen}", (x1, max(25, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)

            target_locked = bool(target and abs(pan_rate) < 1e-9 and abs(tilt_rate) < 1e-9)
            if hardware:
                if hardware.error:
                    raise RuntimeError(f"ESP32 communication failed: {hardware.error}")
                hardware.set_state(pan, tilt, target_locked, hardware_alert, target is not None)

            cv2.putText(annotated, "1 person: auto-follow | [ ] select | F follow | I report/notify | 0 stop | E-stop | Q quit", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 2)
            backend_state = "SIM ESP32" if hardware_backend == "simulated" else "REAL ESP32"
            if auto_follow_inhibited:
                mode = f"E-stop active: tracking inhibited | {backend_state}"
            elif auto_following:
                mode = f"Auto-following only visible track #{selected_id} | {backend_state}"
            elif follow_enabled:
                mode = f"Following selected track #{selected_id} | {backend_state}"
            else:
                mode = f"Multiple people: operator selection required | {backend_state}"
            cv2.putText(annotated, mode, (12, annotated.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
            if now < notification_until:
                cv2.rectangle(annotated, (8, 40), (annotated.shape[1] - 8, 78), (0, 0, 180), -1)
                cv2.putText(annotated, notification_message, (18, 66),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
            right_panel = virtual_camera.overview(raw, crop_bounds) if virtual_camera and crop_bounds else virtual_view(raw, pan, tilt)
            combined = np.hstack((annotated, right_panel))
            cv2.imshow("Multi-person Surveillance — Q to quit", combined)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("["):
                selected_id = choose_track(selected_id, active_ids, -1)
                auto_following = False
            elif key == ord("]"):
                selected_id = choose_track(selected_id, active_ids, 1)
                auto_following = False
            elif key == ord("f") and selected_id is not None:
                follow_enabled = not follow_enabled
                auto_following = False
            elif key == ord("0"):
                follow_enabled = False
                selected_id = None
                auto_following = False
            elif key == ord("u"):
                candidates = [track_id for track_id in active_ids if classify(tracks[track_id]["identity"], authorized) == "UNREGISTERED"]
                selected_id = candidates[0] if candidates else None
                follow_enabled = selected_id is not None
                auto_following = False
            elif key == ord("i"):
                incident = tracks.get(selected_id) if selected_id is not None else None
                if incident is None or selected_id not in active_ids:
                    notification_message = "Select a visible person before reporting an incident"
                    notification_until = time.monotonic() + 5.0
                elif incident["incident_reported"]:
                    notification_message = f"Track #{selected_id} already reported"
                    notification_until = time.monotonic() + 5.0
                else:
                    status = classify(incident["identity"], authorized)
                    record_incident(
                        annotated, selected_id, incident["identity"], status, incident["distance"],
                    )
                    incident["incident_reported"] = True
                    notification_message = f"INCIDENT RECORDED — security notified locally — track #{selected_id}"
                    notification_until = time.monotonic() + 8.0
            elif key == ord("e") and hardware_backend == "simulated":
                bridge = hardware.bridge
                bridge.set_estop(not bridge.board.state.estop_active)
                follow_enabled = False
                selected_id = None
                auto_following = False
                auto_follow_inhibited = bridge.board.state.estop_active
    finally:
        camera.release()
        cv2.destroyAllWindows()
        if hardware:
            hardware.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator-supervised camera tracking and security event capture.")
    parser.add_argument("--source", default="0", help="webcam index, local video path, or OpenCV-supported stream URL (default: 0)")
    parser.add_argument("--camera", type=int, dest="legacy_camera", help="deprecated alias for --source")
    parser.add_argument("--hardware-backend", choices=("real", "simulated"), default="real",
                        help="real ESP32 is the deployment default; simulated is a local development reference")
    parser.add_argument("--esp32-port", help="serial port for --hardware-backend real, for example COM3")
    parser.add_argument("--closed-loop-replay", action="store_true",
                        help="run YOLO on a pan/tilt-driven crop of a wide recorded scene; requires a source wider than the camera FOV")
    parser.add_argument("--scene-fov-x", type=float, default=130.0,
                        help="horizontal FOV represented by the wide replay source in degrees (default: 130)")
    parser.add_argument("--scene-fov-y", type=float, default=90.0,
                        help="vertical FOV represented by the wide replay source in degrees (default: 90)")
    args = parser.parse_args()
    source = str(args.legacy_camera) if args.legacy_camera is not None else args.source
    run(source, args.hardware_backend, args.esp32_port, args.closed_loop_replay, args.scene_fov_x, args.scene_fov_y)


if __name__ == "__main__":
    main()
