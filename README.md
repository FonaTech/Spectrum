# AS7341 Spectrum for MaixCAM2

[中文说明](Readme_CN.md)

A MaixPy spectrometer application for Sipeed MaixCAM2 and the AMS/OSRAM AS7341 11-channel multispectral sensor. It includes GPIO software I2C, AS7341 sampling, touchscreen interaction, a modern local GUI, relative spectrum reconstruction, peak annotation, a lightweight WebUI, and CSV export.

## Screenshots

### WebUI Platform

![AS7341 WebUI platform](Spectrum_Platform.png)

### MaixCAM2 GUI

![AS7341 MaixCAM2 GUI](GUI.png)

## Features

- Real-time MaixCAM2 touchscreen interface
- AS7341 dual-SMUX sampling for F1-F8, Clear, and NIR channels
- GPIO open-drain software I2C on B20/B19 for the current wiring
- Dark baseline zeroing, gain control, integration-time control, pause/run, CSV save, and exit controls
- 380-780 nm visible relative SPD reconstruction with a 0.1 nm internal grid and filled area plot
- Automatic visible-spectrum peak annotation, up to 5 peak wavelengths
- Separate 760-1000 nm IR estimate window, hidden by default and toggled by the top-right `IR` tab
- Lightweight WebUI on port `2932` for remote control, spectrum viewing, and CSV download

## Hardware

Target hardware:

- Sipeed MaixCAM2
- AS7341 spectral sensor module
- 3.3 V I/O wiring

Default wiring:

| AS7341 | MaixCAM2 |
| --- | --- |
| VDD | 3V3 |
| GND | GND |
| SCL | B20 |
| SDA | B19 |
| INT | B18 |
| GPIO | B21 |

B20/B19 are not assumed to be hardware I2C pins on the MaixCAM2 connector. This project uses GPIO open-drain software I2C by default, so the wiring above can be used directly. Do not drive MaixCAM2 GPIO pins with 5 V logic.

## Files

- `main.py`: Self-contained MaixPy application entry point, recommended for direct upload and execution.
- `app.yaml`: MaixPy application descriptor.
- `soft_i2c_gpio.py`: Modular software I2C implementation.
- `as7341_driver.py`: Modular AS7341 driver and spectrum reconstruction code.
- `spectrometer_ui.py`: Modular GUI drawing code.
- `as7341_spectrometer_maixcam2.py`: Modular application entry point.
- `AS7341_DS000504_3-00.pdf`: Local copy of the official AS7341 datasheet.
- `maixcam2_pins.jpg`: MaixCAM2 pin reference image.
- `Spectrum_Platform.png`: WebUI screenshot.
- `GUI.png`: MaixCAM2 GUI screenshot.

For MaixVision or the MaixPy runner, running `main.py` is recommended to avoid missing module upload errors.

## Usage

1. Wire the AS7341 to MaixCAM2 using the default wiring table.
2. Upload and run `main.py` on the MaixCAM2.
3. The app probes the AS7341 automatically at I2C address `0x39`.
4. Use the touchscreen buttons:
   - `Run/Pause`: Start or pause sampling
   - `Zero`: Capture the current dark baseline
   - `Gain -/+`: Adjust AS7341 analog gain
   - `Int -/+`: Adjust integration time
   - `Save`: Append the current sample to `as7341_spectrum_long.csv`
   - `Exit`: Exit the application
5. Tap the top-right `IR` tab to show or hide the IR estimate window.

## WebUI

The application starts a lightweight HTTP service on the MaixCAM2 when possible. The default port is `2932`.

Open this URL from a browser on the same network:

```text
http://<MaixCAM2-IP>:2932/
```

The WebUI supports:

- Live 380-780 nm visible SPD filled curve and peak labels
- Raw and corrected values for each AS7341 channel
- Remote `Run/Pause`, `Zero`, `Gain -/+`, `Int -/+`, `Save`, and `Exit`
- Direct `Download CSV` export of `as7341_spectrum_long.csv`

If the web service fails to start, the local touchscreen application still works.

## Spectrum Reconstruction

The AS7341 is an 11-channel multispectral sensor, not a high-resolution laboratory spectrometer. The reconstructed SPD is a relative spectrum estimate and should not be treated as absolute calibrated irradiance without device-level calibration.

Current algorithm:

- Subtracts the dark baseline from raw channel values.
- Normalizes by gain and integration time.
- Builds a channel response model from the AS7341 datasheet center wavelengths, FWHM values, and typical responses.
- Reconstructs a non-negative smoothed visible SPD from F1-F8 over 380-780 nm on a 0.1 nm grid.
- Uses Clear as a global SPD constraint and records the Clear-normalized signal for calibration.
- Keeps NIR separate from the main SPD and reconstructs a 760-1000 nm IR estimate.
- Detects local visible SPD peaks and labels up to 5 peak wavelengths.
- Caches response matrices, Clear response, and NIR curve shape for speed.
- Downsamples WebUI plot data, while CSV export keeps the full 0.1 nm continuous spectrum.
- Applies a sensor-support prior and peak-support threshold near low-wavelength boundaries to reduce false 380-405 nm edge peaks.

The main SPD is treated as a relative radiant-power spectrum. The AS7341 response data already reflects wavelength-dependent optical/electrical response, so the main plot does not apply an additional `E = hc/lambda` correction. Applying that correction again would risk over-boosting short wavelengths. If photon-flux spectrum or PPFD is needed, convert from the exported relative power spectrum separately.

Lux is an estimate derived from the reconstructed visible SPD:

```text
lux_est ~= 683 * integral(relative_power(lambda) * V(lambda) d_lambda) * SPD_LUX_CALIBRATION
```

`V(lambda)` is an approximate photopic response curve. This was the earlier Lux method and generally gives larger, more visually plausible readings than a direct Clear-channel linear mapping. The CSV still records `clear_lux_signal = corrected_Clear / (gain * integration_ms)` for calibration and comparison.

The default `SPD_LUX_CALIBRATION` is an observation-friendly estimate, not a factory calibration. For trustworthy absolute illuminance, measure the same light source with a calibrated lux meter and set:

```text
SPD_LUX_CALIBRATION = reference_lux / displayed_lux_est
```

For more accurate spectral data, build a device-specific calibration matrix with a standard light source or monochromator and recalibrate for the sensor module, diffuser, optical path, and temperature.

## Data Export

Pressing `Save` appends data to `as7341_spectrum_long.csv`. The export uses a long vertical table where each wavelength occupies one row. Fields include:

- Timestamp, gain, integration time, and saturation state
- Raw, dark, corrected, and normalized data for all 10 exported channels
- Visible SPD summary: dominant wavelength, centroid, estimated CCT, fit confidence, and peak list
- `kind,wavelength_nm,relative_intensity,relative_power`
- `lux_est,lux_source,clear_lux_signal`
- Full 0.1 nm visible SPD data over 380-780 nm
- Full 0.1 nm IR estimate data over 760-1000 nm

## License

New source code in this project is released under the MIT License, copyright (c) 2026 Fona. See [LICENSE](LICENSE).

## Third-Party Notices

This project depends on or references the following third-party projects, hardware documents, and vendor materials. Their copyright and license notices remain in effect and are not relicensed by this project's MIT License.

- MaixPy and MaixCDK  
  Copyright (c) 2023- Sipeed Ltd.  
  Licensed under the Apache License, Version 2.0.  
  Project: <https://github.com/sipeed/MaixPy>

- Adafruit CircuitPython AS7341 driver  
  Copyright (c) 2020 Bryan Siepert for Adafruit Industries.  
  Licensed under the MIT License.  
  Project: <https://github.com/adafruit/Adafruit_CircuitPython_AS7341>

- AS7341 datasheet and sensor documentation  
  AS7341 is a product and trademarked device documentation of AMS/OSRAM or its respective rights holders. The included datasheet is retained as vendor reference documentation and remains under its original copyright.

- MaixCAM2 hardware, pinout image, board names, and product documentation  
  MaixCAM2 and related board documentation belong to Sipeed Ltd. or their respective rights holders. Hardware names and diagrams are used only for compatibility and wiring reference.

## Disclaimer

This software is provided for research, education, and prototyping. It is not certified for safety-critical, medical, industrial metrology, or regulatory measurement use. Validate hardware, calibration, optical path, and exported spectral data before relying on results.
