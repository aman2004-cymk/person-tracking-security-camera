"""Local face-recognition attendance for the pan-tilt camera project.

Faces are recognised only from labelled images in attendance_data/<person_name>/.
Run this after enrolling people with capture_attendance_data.py.  A person is
marked once per calendar day after several consecutive matching frames.

Requirements:
    py -m pip install opencv-contrib-python numpy
"""
import argparse
import csv
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "attendance_data"
MODEL_DIR = PROJECT_DIR / "attendance_model"
MODEL_PATH = MODEL_DIR / "lbph_faces.yml"
LABELS_PATH = MODEL_DIR / "labels.csv"
ATTENDANCE_DIR = PROJECT_DIR / "attendance"
FACE_SIZE = (160, 160)
FACE_SCALE = 1.15
MIN_NEIGHBORS = 5
MATCH_THRESHOLD = 62.0  # LBPH distance: lower means a closer match.
CONFIRMATION_FRAMES = 5
# A face smaller than this may be detected, but has too little detail for a
# trustworthy LBPH identity decision at the current camera resolution.
MIN_RECOGNITION_FACE_PIXELS = 100
MIN_FACE_SHARPNESS = 65.0  # Variance of the Laplacian; lower means blurrier.
PROJECT_CASCADE_PATH = PROJECT_DIR / "assets" / "haarcascade_frontalface_default.xml"
PROJECT_PROFILE_CASCADE_PATH = PROJECT_DIR / "assets" / "haarcascade_profileface.xml"


def safe_person_name(name: str) -> str:
    """Return a filesystem-safe display name, or reject an empty result."""
    result = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip()
    if not result:
        raise ValueError("Use a name containing letters, numbers, spaces, _ or -.")
    return result


def _load_cascade(filename: str, project_path: Path) -> object | None:
    """Load an OpenCV cascade from bundled data or the project fallback."""
    bundled_path = Path(cv2.data.haarcascades) / filename
    for cascade_path in (bundled_path, project_path):
        if cascade_path.is_file():
            detector = cv2.CascadeClassifier(str(cascade_path))
            if not detector.empty():
                return detector
    return None


def _iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[0] + first[2], second[0] + second[2]), min(first[1] + first[3], second[1] + second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union else 0.0


class FaceDetector:
    """Frontal plus mirrored left/right profile cascade detector."""

    def __init__(self, frontal: object, profile: object | None):
        self.frontal = frontal
        self.profile = profile

    def detectMultiScale(self, gray: np.ndarray, **kwargs) -> np.ndarray:
        found = [tuple(map(int, item)) for item in self.frontal.detectMultiScale(gray, **kwargs)]
        if self.profile is not None:
            found.extend(tuple(map(int, item)) for item in self.profile.detectMultiScale(gray, **kwargs))
            mirrored = cv2.flip(gray, 1)
            width = gray.shape[1]
            found.extend((width - x - w, y, w, h)
                         for x, y, w, h in self.profile.detectMultiScale(mirrored, **kwargs))
        # Keep only the largest result where frontal/profile cascades overlap.
        kept: list[tuple[int, int, int, int]] = []
        for bounds in sorted(found, key=lambda item: item[2] * item[3], reverse=True):
            if all(_iou(bounds, prior) < 0.35 for prior in kept):
                kept.append(bounds)
        return np.array(kept, dtype=np.int32).reshape((-1, 4)) if kept else np.empty((0, 4), dtype=np.int32)


def face_detector() -> FaceDetector:
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        raise RuntimeError(
            "OpenCV cascade support is missing. Install opencv-contrib-python to use face recognition."
        )
    # Some OpenCV builds (including certain Python 3.14 distributions) omit
    # XML data. Project-local fallbacks keep enrollment reproducible.
    frontal = _load_cascade("haarcascade_frontalface_default.xml", PROJECT_CASCADE_PATH)
    if frontal is None:
        raise RuntimeError(
        "Face cascade unavailable. Install OpenCV's data files or place "
        f"haarcascade_frontalface_default.xml at {PROJECT_CASCADE_PATH}."
    )
    profile = _load_cascade("haarcascade_profileface.xml", PROJECT_PROFILE_CASCADE_PATH)
    return FaceDetector(frontal, profile)


def normalise_face(gray: np.ndarray, bounds) -> np.ndarray:
    x, y, w, h = bounds
    return cv2.resize(gray[y:y + h, x:x + w], FACE_SIZE, interpolation=cv2.INTER_AREA)


def face_quality(gray: np.ndarray, bounds) -> tuple[bool, str, float]:
    """Return whether a face crop is usable for identity recognition.

    Detection and recognition are deliberately separated: a distant face can
    still be shown to the operator without creating a low-confidence identity
    decision or attendance entry.
    """
    x, y, width, height = map(int, bounds)
    pixels = min(width, height)
    if pixels < MIN_RECOGNITION_FACE_PIXELS:
        return False, f"MOVE CLOSER ({pixels}px / {MIN_RECOGNITION_FACE_PIXELS}px)", 0.0
    crop = gray[y:y + height, x:x + width]
    sharpness = float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else 0.0
    if sharpness < MIN_FACE_SHARPNESS:
        return False, "HOLD STILL / IMPROVE LIGHT", sharpness
    return True, "QUALITY OK", sharpness


def train_model() -> dict[int, str]:
    """Train a local LBPH recogniser from attendance_data/<name> image folders."""
    if not hasattr(cv2, "face"):
        raise RuntimeError(
            "OpenCV face support is missing. Install opencv-contrib-python, then restart Python."
        )
    detector = face_detector()
    images, numeric_labels, labels = [], [], {}
    people = sorted(folder for folder in DATA_DIR.iterdir() if folder.is_dir()) if DATA_DIR.exists() else []
    for label, folder in enumerate(people):
        person = safe_person_name(folder.name)
        usable = 0
        for image_path in folder.glob("*.*"):
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            faces = detector.detectMultiScale(image, scaleFactor=FACE_SCALE, minNeighbors=MIN_NEIGHBORS)
            if len(faces) == 0:
                continue
            # Use the largest face so accidental background faces do not train a label.
            bounds = max(faces, key=lambda rect: rect[2] * rect[3])
            images.append(normalise_face(image, bounds))
            numeric_labels.append(label)
            usable += 1
        if usable:
            labels[label] = person
            print(f"{person}: {usable} usable training images")
        else:
            print(f"Warning: no usable face image found for {person}; skipped.")
    if len(labels) < 1:
        raise RuntimeError(
            f"No faces to train. Add clear face photos below {DATA_DIR}\\<person name>\\ first."
        )
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, np.array(numeric_labels, dtype=np.int32))
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    recognizer.write(str(MODEL_PATH))
    with LABELS_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["label", "person_name"])
        writer.writerows(sorted(labels.items()))
    print(f"Saved model for {len(labels)} people to {MODEL_PATH}")
    return labels


def load_model() -> tuple[object, dict[int, str]]:
    if not MODEL_PATH.exists() or not LABELS_PATH.exists():
        labels = train_model()
    else:
        with LABELS_PATH.open(newline="", encoding="utf-8") as file:
            labels = {int(row["label"]): row["person_name"] for row in csv.DictReader(file)}
    if not hasattr(cv2, "face"):
        raise RuntimeError("Install opencv-contrib-python to use face recognition.")
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(str(MODEL_PATH))
    return recognizer, labels


def attendance_path(now: datetime) -> Path:
    return ATTENDANCE_DIR / f"attendance_{now:%Y-%m-%d}.csv"


def already_present(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as file:
        return {row["person_name"] for row in csv.DictReader(file)}


def mark_present(person: str, distance: float, present: set[str]) -> bool:
    now = datetime.now()
    path = attendance_path(now)
    if person in present:
        return False
    ATTENDANCE_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["timestamp", "person_name", "match_distance", "status"])
        if new_file:
            writer.writeheader()
        writer.writerow({
            "timestamp": now.isoformat(timespec="seconds"),
            "person_name": person,
            "match_distance": f"{distance:.2f}",
            "status": "PRESENT",
        })
    present.add(person)
    print(f"Marked present: {person} ({now:%H:%M:%S})")
    return True


def run(camera_index: int, retrain: bool) -> None:
    if retrain:
        train_model()
    recognizer, labels = load_model()
    detector = face_detector()
    today_path = attendance_path(datetime.now())
    present = already_present(today_path)
    votes: Counter[str] = Counter()
    cv2.namedWindow("Face Attendance — Q to quit", cv2.WINDOW_NORMAL)
    camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(camera_index)
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera index {camera_index}.")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    last_banner, banner_until = "Scanning enrolled faces", 0.0
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Detect smaller faces so the operator receives a useful distance
            # warning before recognition becomes possible.
            faces = detector.detectMultiScale(gray, scaleFactor=FACE_SCALE, minNeighbors=MIN_NEIGHBORS, minSize=(40, 40))
            seen_this_frame = set()
            for bounds in faces:
                x, y, w, h = bounds
                quality_ok, quality_text, _ = face_quality(gray, bounds)
                if not quality_ok:
                    colour, text = (0, 80, 255), quality_text
                else:
                    label, distance = recognizer.predict(normalise_face(gray, bounds))
                    person = labels.get(label) if distance <= MATCH_THRESHOLD else None
                    if person:
                        seen_this_frame.add(person)
                        votes[person] += 1
                        ready = votes[person] >= CONFIRMATION_FRAMES
                        if ready and mark_present(person, distance, present):
                            last_banner, banner_until = f"PRESENT: {person}", time.monotonic() + 2.5
                        colour, text = (40, 220, 40), f"{person}  ({distance:.0f})"
                    else:
                        colour, text = (0, 165, 255), "Unknown face"
                cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2)
                cv2.putText(frame, text, (x, max(25, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, colour, 2)
            # Consecutive frames prevent a single, low-quality detection marking attendance.
            for person in list(votes):
                if person not in seen_this_frame:
                    votes[person] = 0
            banner = last_banner if time.monotonic() < banner_until else "Scanning enrolled faces"
            cv2.putText(frame, banner, (14, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (40, 220, 40), 2)
            cv2.putText(frame, f"Present today: {len(present)} | min face: {MIN_RECOGNITION_FACE_PIXELS}px | Q: quit", (14, frame.shape[0] - 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            cv2.imshow("Face Attendance — Q to quit", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="Recognise enrolled faces and mark daily attendance.")
    parser.add_argument("--camera", type=int, default=0, help="webcam index (default: 0)")
    parser.add_argument("--retrain", action="store_true", help="rebuild the face model before scanning")
    args = parser.parse_args()
    run(args.camera, args.retrain)


if __name__ == "__main__":
    main()
