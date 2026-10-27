"""Capture labelled local face samples for face_attendance.py.

Example: py capture_attendance_data.py --name ""Ada Lovelace""
Press S to save a detected face and Q to finish. Capture 25-50 varied samples.
"""
import argparse
from pathlib import Path

import cv2

from face_attendance import DATA_DIR, FACE_SCALE, MIN_NEIGHBORS, face_detector, face_quality, normalise_face, safe_person_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture labelled enrollment photos for local attendance.")
    parser.add_argument("--name", required=True, help="person's display name")
    parser.add_argument("--camera", type=int, default=0, help="webcam index (default: 0)")
    args = parser.parse_args()
    name = safe_person_name(args.name)
    output = DATA_DIR / name
    output.mkdir(parents=True, exist_ok=True)
    next_number = len(list(output.glob("*.png")))
    detector = face_detector()
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera index {args.camera}.")
    print(f"Saving samples for {name} in {output}. Press S to save, Q to finish.")
    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=FACE_SCALE, minNeighbors=MIN_NEIGHBORS, minSize=(90, 90))
            best = max(faces, key=lambda rect: rect[2] * rect[3]) if len(faces) else None
            if best is not None:
                x, y, w, h = best
                quality_ok, quality_text, _ = face_quality(gray, best)
                colour = (40, 220, 40) if quality_ok else (0, 80, 255)
                cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2)
                cv2.putText(frame, quality_text, (x, max(55, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.52, colour, 2)
            cv2.putText(frame, f"{name}: {next_number} samples | S: save | Q: finish", (12, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
            cv2.imshow("Enrollment — Face Attendance", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s") and best is not None and face_quality(gray, best)[0]:
                face = normalise_face(gray, best)
                cv2.imwrite(str(output / f"face_{next_number:03d}.png"), face)
                next_number += 1
                print(f"Saved sample {next_number}")
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
