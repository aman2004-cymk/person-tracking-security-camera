# Person-Tracking Security Camera — Project Brief

## Goal

Build a supervised security-camera prototype that detects and tracks people,
follows a visible person with an ESP32-controlled pan/tilt mount, and records
local events for operator review. Optional face matching is advisory only.

## Current state

- Production-oriented Python camera/tracking application and ESP32 firmware.
- Local OpenCV operator window and loopback-only event-review dashboard.
- Serial commands are bounded; the PC checks firmware acknowledgements.
- Setup, wiring, and bench commissioning steps are documented in
  [README.md](README.md) and [HARDWARE_BUILD.md](HARDWARE_BUILD.md).
- Simulation and private runtime data are excluded from the production Git
  package.
- Private GitHub repository: https://github.com/aman2004-cymk/person-tracking-security-camera
- Published branch: `master`; latest published commit: `4ffc6b7`.

## Quick setup

1. Clone the private GitHub repository and open its folder in PowerShell.
2. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Configure the local authorization list from
   `authorized_people.example.csv`. Enroll face data only with informed
   consent; retain a manual verification option.
4. Install Arduino IDE, ESP32 board support, and `ESP32Servo`; upload
   `firmware/esp32_pan_tilt_controller.ino`.
5. Follow the unloaded-servo checks in `HARDWARE_BUILD.md` before attaching the
   camera or using the system near people.
6. Launch with the actual board's serial port:

   ```powershell
   python multi_person_surveillance.py --source 0 --hardware-backend real --esp32-port COM3
   ```

7. In another terminal, optionally start the local event dashboard:

   ```powershell
   python dashboard.py
   ```

   Open `http://127.0.0.1:8765/`. It is loopback-only and is not a remote
   dashboard or live camera control panel.

## Verification completed

- 22 automated tests passed at the time of handover.
- Synthetic replay integration and a short live-camera tracking check passed.
- Wokwi firmware compiled; startup stop, bounds clamping, command/LED
  acknowledgements, explicit stop, and watchdog stop were observed.
- The dashboard HTTP/API smoke test passed.

## Still required before real deployment

- Bench-test the physical ESP32, servos, power supply, E-stop, angle limits,
  and USB-loss/watchdog behavior.
- Run a supervised end-to-end test with the actual mounted camera.
- Evaluate face matching under real lighting if enabled; do not make access
  decisions solely from a model match.
- Decide on retention, access control, notice, consent, and local legal
  requirements before any pilot.
- Cloud/IoT alerts and remote dashboard access are not implemented.

**Status:** software and documentation are prepared; physical deployment is
not yet validated or certified.
