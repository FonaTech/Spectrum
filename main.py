# Auto-merged single-file MaixPy app from local modules.
# Source modules: soft_i2c_gpio.py, as7341_driver.py, spectrometer_ui.py, as7341_spectrometer_maixcam2.py

import math

try:
    import socket
except Exception:
    import usocket as socket

try:
    import json
except Exception:
    try:
        import ujson as json
    except Exception:
        json = None

# ===== soft_i2c_gpio.py =====
try:
    from maix import err, gpio, pinmap, time
except Exception:
    err = None
    gpio = None
    pinmap = None
    time = None


class SoftI2CError(Exception):
    pass


class SoftI2C:
    """Small GPIO bit-banged I2C master for MaixPy.

    The MaixCAM2 pins used in this project, B20/B19, are SPI pins on the
    connector diagram, so hardware I2C is not assumed. Both lines are driven as
    open-drain outputs and released high for ACK/read phases.
    """

    def __init__(self, scl_pin="B20", sda_pin="B19", freq=80000):
        if gpio is None or pinmap is None or time is None:
            raise SoftI2CError("This module must run inside MaixPy")
        self.scl_pin = scl_pin
        self.sda_pin = sda_pin
        self.freq = max(10000, min(150000, int(freq)))
        self.delay_us = max(2, int(500000 // self.freq))
        self.scl_name = self._setup_gpio_pin(scl_pin)
        self.sda_name = self._setup_gpio_pin(sda_pin)
        self.scl = gpio.GPIO(self.scl_name, gpio.Mode.OUT_OD, gpio.Pull.PULL_UP)
        self.sda = gpio.GPIO(self.sda_name, gpio.Mode.OUT_OD, gpio.Pull.PULL_UP)
        self.release()

    def _setup_gpio_pin(self, pin):
        names = self._candidate_gpio_names(pin)
        last_error = None
        for name in names:
            try:
                result = pinmap.set_pin_function(pin, name)
                if err is not None and result is not None:
                    err.check_raise(result, "set pin failed")
                return name
            except Exception as exc:
                last_error = exc
        if last_error:
            raise SoftI2CError("set pin function failed: %s -> %s" % (pin, names))
        return pin

    def _candidate_gpio_names(self, pin):
        if pin.startswith("GPIO"):
            return [pin]
        bank = pin[0]
        num = pin[1:]
        names = []
        if bank in ("A", "B", "C", "D") and num:
            names.append("GPIO%s%s" % (bank, num))
        names.append(pin)
        return names

    def _sleep(self):
        time.sleep_us(self.delay_us)

    def _set_scl(self, value):
        self.scl.value(1 if value else 0)

    def _set_sda(self, value):
        self.sda.value(1 if value else 0)

    def _read_scl(self):
        return 1 if self.scl.value() else 0

    def _read_sda(self):
        return 1 if self.sda.value() else 0

    def release(self):
        self._set_sda(1)
        self._set_scl(1)
        self._sleep()

    def _clock_high(self):
        self._set_scl(1)
        timeout = 500
        while not self._read_scl() and timeout > 0:
            time.sleep_us(1)
            timeout -= 1
        if timeout <= 0:
            raise SoftI2CError("SCL held low")
        self._sleep()

    def start(self):
        self._set_sda(1)
        self._clock_high()
        self._set_sda(0)
        self._sleep()
        self._set_scl(0)
        self._sleep()

    def stop(self):
        self._set_sda(0)
        self._sleep()
        self._clock_high()
        self._set_sda(1)
        self._sleep()

    def write_byte(self, value):
        value &= 0xFF
        for bit in range(7, -1, -1):
            self._set_sda((value >> bit) & 1)
            self._sleep()
            self._clock_high()
            self._set_scl(0)
            self._sleep()
        self._set_sda(1)
        self._sleep()
        self._clock_high()
        ack = self._read_sda() == 0
        self._set_scl(0)
        self._sleep()
        return ack

    def read_byte(self, ack=True):
        value = 0
        self._set_sda(1)
        for _ in range(8):
            value <<= 1
            self._clock_high()
            if self._read_sda():
                value |= 1
            self._set_scl(0)
            self._sleep()
        self._set_sda(0 if ack else 1)
        self._sleep()
        self._clock_high()
        self._set_scl(0)
        self._set_sda(1)
        self._sleep()
        return value

    def writeto(self, address, data):
        self.start()
        try:
            if not self.write_byte((address << 1) | 0):
                raise SoftI2CError("I2C address 0x%02X did not ACK write" % address)
            for value in data:
                if not self.write_byte(value):
                    raise SoftI2CError("I2C data byte did not ACK")
        finally:
            self.stop()

    def readfrom(self, address, length):
        self.start()
        try:
            if not self.write_byte((address << 1) | 1):
                raise SoftI2CError("I2C address 0x%02X did not ACK read" % address)
            out = []
            for index in range(length):
                out.append(self.read_byte(ack=index < length - 1))
            return out
        finally:
            self.stop()

    def write_reg(self, address, reg, values):
        if isinstance(values, int):
            values = [values]
        self.writeto(address, [reg & 0xFF] + [v & 0xFF for v in values])

    def read_reg(self, address, reg, length=1):
        self.start()
        try:
            if not self.write_byte((address << 1) | 0):
                raise SoftI2CError("I2C address 0x%02X did not ACK register write" % address)
            if not self.write_byte(reg & 0xFF):
                raise SoftI2CError("I2C register 0x%02X did not ACK" % reg)
            self.start()
            if not self.write_byte((address << 1) | 1):
                raise SoftI2CError("I2C address 0x%02X did not ACK register read" % address)
            out = []
            for index in range(length):
                out.append(self.read_byte(ack=index < length - 1))
            return out
        finally:
            self.stop()

    def scan(self, start=0x08, end=0x78):
        found = []
        for address in range(start, end):
            try:
                self.start()
                if self.write_byte((address << 1) | 0):
                    found.append(address)
            except Exception:
                pass
            finally:
                self.stop()
        return found

# ===== as7341_driver.py =====
try:
    from maix import time
except Exception:
    time = None



class AS7341Error(Exception):
    pass


def _make_nm_grid(start, end, step):
    count = int(round((float(end) - float(start)) / float(step))) + 1
    return [round(float(start) + i * float(step), 1) for i in range(count)]


class AS7341:
    ADDRESS = 0x39

    REG_CONFIG = 0x70
    REG_STAT = 0x71
    REG_ENABLE = 0x80
    REG_ATIME = 0x81
    REG_ID = 0x92
    REG_STATUS = 0x93
    REG_ASTATUS = 0x94
    REG_CH0_DATA = 0x95
    REG_STATUS2 = 0xA3
    REG_CFG0 = 0xA9
    REG_CFG1 = 0xAA
    REG_CFG6 = 0xAF
    REG_CFG8 = 0xB1
    REG_ASTEP_L = 0xCA
    REG_ASTEP_H = 0xCB
    REG_AZ_CONFIG = 0xD6

    ENABLE_PON = 0x01
    ENABLE_SP_EN = 0x02
    ENABLE_SMUXEN = 0x10

    GAIN_STEPS = [
        (0, 0.5),
        (1, 1),
        (2, 2),
        (3, 4),
        (4, 8),
        (5, 16),
        (6, 32),
        (7, 64),
        (8, 128),
        (9, 256),
        (10, 512),
    ]

    CHANNELS = [
        ("F1", 415),
        ("F2", 445),
        ("F3", 480),
        ("F4", 515),
        ("F5", 555),
        ("F6", 590),
        ("F7", 630),
        ("F8", 680),
        ("Clear", 0),
        ("NIR", 910),
    ]

    SPECTRAL_BANDS = [
        (415, 26, 55),
        (445, 30, 110),
        (480, 36, 210),
        (515, 39, 390),
        (555, 39, 680),
        (590, 40, 840),
        (630, 50, 1350),
        (680, 52, 1070),
    ]
    CLEAR_BAND = (560, 420, 1750)
    NIR_BAND = (910, 90, 112)
    GRID_STEP_NM = 0.1
    RESPONSE_EPS = 1e-7
    INVERSE_ITERATIONS = 10
    SMOOTH_RADIUS_NM = 1.2
    SMOOTH_BLEND = 0.55
    SUPPORT_PRIOR_STRENGTH = 0.18
    SUPPORT_PRIOR_FLOOR = 0.06
    PEAK_SUPPORT_MIN = 0.12
    SPD_LUX_CALIBRATION = 0.02
    CLEAR_LUX_CALIBRATION = 100.0
    SPECTRUM_GRID = _make_nm_grid(380.0, 780.0, GRID_STEP_NM)
    IR_GRID = _make_nm_grid(760.0, 1000.0, GRID_STEP_NM)
    _VISIBLE_MODEL_CACHE = None
    _CLEAR_ROW_CACHE = None
    _IR_SHAPE_CACHE = None
    _PHOTOPIC_WEIGHT_CACHE = None

    LOW_SMUX = [
        (0x00, 0x30),
        (0x01, 0x01),
        (0x02, 0x00),
        (0x03, 0x00),
        (0x04, 0x00),
        (0x05, 0x42),
        (0x06, 0x00),
        (0x07, 0x00),
        (0x08, 0x50),
        (0x09, 0x00),
        (0x0A, 0x00),
        (0x0B, 0x00),
        (0x0C, 0x20),
        (0x0D, 0x04),
        (0x0E, 0x00),
        (0x0F, 0x30),
        (0x10, 0x01),
        (0x11, 0x50),
        (0x12, 0x00),
        (0x13, 0x06),
    ]

    HIGH_SMUX = [
        (0x00, 0x00),
        (0x01, 0x00),
        (0x02, 0x00),
        (0x03, 0x40),
        (0x04, 0x02),
        (0x05, 0x00),
        (0x06, 0x10),
        (0x07, 0x03),
        (0x08, 0x50),
        (0x09, 0x10),
        (0x0A, 0x03),
        (0x0B, 0x00),
        (0x0C, 0x00),
        (0x0D, 0x00),
        (0x0E, 0x24),
        (0x0F, 0x00),
        (0x10, 0x00),
        (0x11, 0x50),
        (0x12, 0x00),
        (0x13, 0x06),
    ]

    def __init__(self, bus, address=ADDRESS):
        self.bus = bus
        self.address = address
        self.atime = 29
        self.astep = 599
        self.gain_index = 8
        self.dark = [0] * len(self.CHANNELS)
        self.last_sample = None

    def begin(self):
        device_id = self.read8(self.REG_ID)
        if ((device_id >> 2) & 0x3F) != 0x09:
            raise AS7341Error("AS7341 not found, ID register=0x%02X" % device_id)
        self.write8(self.REG_ENABLE, self.ENABLE_PON)
        self.sleep_ms(5)
        self.write_low8(self.REG_CONFIG, 0x00)
        self.set_integration(50)
        self.set_gain_index(self.gain_index)
        self.write8(self.REG_AZ_CONFIG, 0xFF)
        self.write8(self.REG_CFG8, 0x00)
        self.clear_status()
        return True

    def sleep_ms(self, ms):
        if time:
            time.sleep_ms(int(ms))

    def ticks_ms(self):
        if time:
            return time.ticks_ms()
        return 0

    def write8(self, reg, value):
        self.bus.write_reg(self.address, reg, value & 0xFF)

    def read8(self, reg):
        return self.bus.read_reg(self.address, reg, 1)[0]

    def read_block(self, reg, length):
        return self.bus.read_reg(self.address, reg, length)

    def write16(self, reg, value):
        self.bus.write_reg(self.address, reg, [value & 0xFF, (value >> 8) & 0xFF])

    def _set_low_register_bank(self, enabled):
        cfg0 = self.read8(self.REG_CFG0)
        if enabled:
            cfg0 |= 0x10
        else:
            cfg0 &= ~0x10
        self.write8(self.REG_CFG0, cfg0)

    def write_low8(self, reg, value):
        self._set_low_register_bank(True)
        try:
            self.write8(reg, value)
        finally:
            self._set_low_register_bank(False)

    def read_low8(self, reg):
        self._set_low_register_bank(True)
        try:
            return self.read8(reg)
        finally:
            self._set_low_register_bank(False)

    def clear_status(self):
        try:
            status = self.read8(self.REG_STATUS)
            if status:
                self.write8(self.REG_STATUS, status)
        except SoftI2CError:
            raise
        except Exception:
            pass

    def set_gain_index(self, index):
        index = max(0, min(len(self.GAIN_STEPS) - 1, int(index)))
        self.gain_index = index
        self.write8(self.REG_CFG1, self.GAIN_STEPS[index][0])

    def gain_value(self):
        return self.GAIN_STEPS[self.gain_index][1]

    def set_integration_registers(self, atime, astep):
        atime = max(0, min(255, int(atime)))
        astep = max(1, min(65534, int(astep)))
        self.atime = atime
        self.astep = astep
        self.write8(self.REG_ATIME, atime)
        self.write16(self.REG_ASTEP_L, astep)

    def set_integration(self, integration_ms):
        integration_ms = max(5, min(1000, int(integration_ms)))
        astep = 599
        atime = int((integration_ms * 1000.0) / ((astep + 1) * 2.78) - 1)
        if atime > 255:
            atime = 255
            astep = int((integration_ms * 1000.0) / ((atime + 1) * 2.78) - 1)
        self.set_integration_registers(atime, astep)

    def integration_ms(self):
        return (self.atime + 1) * (self.astep + 1) * 0.00278

    def adc_full_scale(self):
        return min(65535, (self.atime + 1) * (self.astep + 1))

    def _set_sp_en(self, enabled):
        value = self.read8(self.REG_ENABLE)
        if enabled:
            value |= self.ENABLE_SP_EN | self.ENABLE_PON
        else:
            value &= ~self.ENABLE_SP_EN
            value |= self.ENABLE_PON
        self.write8(self.REG_ENABLE, value)

    def _write_smux(self, table):
        self._set_sp_en(False)
        self.write8(self.REG_CFG0, 0x00)
        for reg, value in table:
            self.write8(reg, value)
        self.write8(self.REG_CFG6, 0x10)
        value = self.read8(self.REG_ENABLE)
        self.write8(self.REG_ENABLE, (value | self.ENABLE_SMUXEN | self.ENABLE_PON) & ~self.ENABLE_SP_EN)
        start = self.ticks_ms()
        while self.read8(self.REG_ENABLE) & self.ENABLE_SMUXEN:
            if self.ticks_ms() - start > 250:
                raise AS7341Error("SMUX command timeout")
            self.sleep_ms(1)

    def _read_adc_once(self, table):
        self._write_smux(table)
        self.clear_status()
        self._set_sp_en(True)
        timeout = max(250, int(self.integration_ms() + 80))
        start = self.ticks_ms()
        while True:
            status2 = self.read8(self.REG_STATUS2)
            if status2 & 0x40:
                break
            if self.ticks_ms() - start > timeout:
                self._set_sp_en(False)
                raise AS7341Error("spectral data timeout")
            self.sleep_ms(1)
        raw = self.read_block(self.REG_ASTATUS, 13)
        self._set_sp_en(False)
        values = []
        for index in range(6):
            lo = raw[1 + index * 2]
            hi = raw[2 + index * 2]
            values.append(lo | (hi << 8))
        return raw[0], values

    def read_raw(self):
        status_low, low = self._read_adc_once(self.LOW_SMUX)
        status_high, high = self._read_adc_once(self.HIGH_SMUX)
        values = [
            low[0],
            low[1],
            low[2],
            low[3],
            high[0],
            high[1],
            high[2],
            high[3],
            int((low[4] + high[4]) / 2),
            int((low[5] + high[5]) / 2),
        ]
        sample = {
            "raw": values,
            "status_low": status_low,
            "status_high": status_high,
            "saturated": bool((status_low | status_high) & 0x80),
            "gain": self.gain_value(),
            "gain_index": self.gain_index,
            "integration_ms": self.integration_ms(),
            "full_scale": self.adc_full_scale(),
        }
        sample["corrected"] = self.apply_dark(values)
        sample["normalized"] = self.normalize(sample["corrected"])
        sample["spectrum"] = self.reconstruct_spectrum(sample["corrected"])
        sample["peak"] = self.peak_channel(sample["corrected"])
        self.last_sample = sample
        return sample

    def apply_dark(self, values):
        return [max(0, int(v) - int(d)) for v, d in zip(values, self.dark)]

    def normalize(self, values):
        max_value = max(max(values), 1)
        return [v / max_value for v in values]

    def peak_channel(self, values):
        visible = values[:8]
        index = visible.index(max(visible)) if visible else 0
        name, wavelength = self.CHANNELS[index]
        return {"name": name, "wavelength": wavelength, "value": visible[index]}

    def reconstruct_spectrum(self, corrected):
        grid = self.SPECTRUM_GRID
        bands = self.SPECTRAL_BANDS
        measured = [corrected[i] for i in range(8)]
        exposure = max(0.001, float(self.gain_value()) * float(self.integration_ms()))
        y = [max(0.0, float(v)) / exposure for v in measured]
        if max(y) <= 0:
            empty = self._empty_spectrum(grid)
            lux_est, clear_lux_signal = self._estimate_lux_from_clear(corrected, exposure)
            empty["lux_est"] = lux_est
            empty["clear_lux_signal"] = clear_lux_signal
            empty["lux_source"] = "clear_fallback"
            empty["ir"] = self._reconstruct_ir(corrected, exposure)
            return empty

        max_sensitivity = max([b[2] for b in bands])
        ycorr = []
        model = self._visible_model()
        rows = model["rows"]
        inv_denominator = model["inv_denominator"]
        support_prior = model["support_prior"]
        peak_support = model["peak_support"]
        for index, band in enumerate(bands):
            _, _, sensitivity = band
            sensitivity_scale = max(0.01, float(sensitivity) / max_sensitivity)
            ycorr.append(y[index] / sensitivity_scale)

        x = self._initial_spectrum(rows, ycorr, grid, inv_denominator, support_prior)
        for _ in range(self.INVERSE_ITERATIONS):
            x = self._multiplicative_update(x, rows, ycorr, inv_denominator, support_prior)
            x = self._smooth_spectrum(x)

        x = self._apply_clear_constraint(x, corrected[8], max_sensitivity, exposure, grid)
        prediction = self._predict_channels(x, rows)
        fit_error = self._fit_error(prediction, ycorr)
        summary = self._spectrum_summary(grid, x, corrected, fit_error, exposure, peak_support)
        summary["peaks"] = self._find_spectrum_peaks(grid, summary["values"], peak_support, limit=5)
        summary["ir"] = self._reconstruct_ir(corrected, exposure)
        return summary

    def _empty_spectrum(self, grid):
        return {
            "grid": list(grid),
            "values": [0.0 for _ in grid],
            "power": [0.0 for _ in grid],
            "dominant_nm": 0,
            "centroid_nm": 0,
            "lux_est": 0.0,
            "clear_lux_signal": 0.0,
            "lux_source": "spd_photopic",
            "cct_est": 0,
            "nir_ratio": 0.0,
            "clear_ratio": 0.0,
            "fit_confidence": 0.0,
            "peaks": [],
            "ir": {
                "grid": list(self.IR_GRID),
                "values": [0.0 for _ in self.IR_GRID],
                "power": [0.0 for _ in self.IR_GRID],
                "peak_nm": 0,
                "relative": 0.0,
            },
        }

    def _response_row(self, center, fwhm, grid):
        sigma = max(1.0, float(fwhm) / 2.355)
        row = []
        total = 0.0
        for wavelength in grid:
            value = math.exp(-0.5 * ((float(wavelength) - center) / sigma) ** 2)
            row.append(value)
            total += value
        if total <= 0:
            return [0.0 for _ in grid]
        return [value / total for value in row]

    def _visible_model(self):
        cached = AS7341._VISIBLE_MODEL_CACHE
        if cached:
            return cached
        grid = self.SPECTRUM_GRID
        size = len(grid)
        rows = []
        denominator = [0.0] * size
        for center, fwhm, _ in self.SPECTRAL_BANDS:
            start, weights = self._sparse_response_row(center, fwhm, grid)
            rows.append((start, weights))
            for offset, weight in enumerate(weights):
                denominator[start + offset] += weight
        inv_denominator = []
        for value in denominator:
            inv_denominator.append(1.0 / value if value > 1e-12 else 0.0)
        cached = {
            "rows": rows,
            "inv_denominator": inv_denominator,
            "support_prior": self._support_prior(denominator),
            "peak_support": self._peak_support(denominator),
        }
        AS7341._VISIBLE_MODEL_CACHE = cached
        return cached

    def _peak_support(self, denominator):
        max_value = max(max(denominator), 1e-12)
        return [max(0.0, min(1.0, value / max_value)) for value in denominator]

    def _support_prior(self, denominator):
        support = self._peak_support(denominator)
        floor = self.SUPPORT_PRIOR_FLOOR
        return [floor + (1.0 - floor) * (value ** 0.65) for value in support]

    def _sparse_response_row(self, center, fwhm, grid):
        sigma = max(1.0, float(fwhm) / 2.355)
        raw = []
        total = 0.0
        for wavelength in grid:
            value = math.exp(-0.5 * ((float(wavelength) - center) / sigma) ** 2)
            raw.append(value)
            total += value
        if total <= 0:
            return 0, []
        threshold = total * self.RESPONSE_EPS
        start = 0
        end = len(raw) - 1
        while start <= end and raw[start] <= threshold:
            start += 1
        while end >= start and raw[end] <= threshold:
            end -= 1
        if start > end:
            return 0, []
        return start, [raw[index] / total for index in range(start, end + 1)]

    def _initial_spectrum(self, rows, ycorr, grid, inv_denominator, support_prior):
        out = [0.0] * len(grid)
        for row_index, item in enumerate(rows):
            start, weights = item
            scale = ycorr[row_index]
            if scale <= 0:
                continue
            for offset, weight in enumerate(weights):
                out[start + offset] += weight * scale
        for index, value in enumerate(out):
            inv = inv_denominator[index]
            out[index] = value * inv if inv > 0 else 0.0
        out = self._apply_support_prior(out, support_prior)
        return self._smooth_spectrum(out)

    def _multiplicative_update(self, spectrum, rows, ycorr, inv_denominator, support_prior):
        prediction = self._predict_channels(spectrum, rows)
        numerator = [0.0] * len(spectrum)
        for row_index, item in enumerate(rows):
            start, weights = item
            ratio = ycorr[row_index] / max(prediction[row_index], 1e-12)
            if ratio <= 0:
                continue
            for offset, weight in enumerate(weights):
                numerator[start + offset] += weight * ratio
        updated = [0.0] * len(spectrum)
        for index, value in enumerate(spectrum):
            inv = inv_denominator[index]
            factor = numerator[index] * inv if inv > 0 else 1.0
            if factor <= 0 or value <= 0:
                updated[index] = 0.0
            else:
                updated[index] = value * factor
        return self._apply_support_prior(updated, support_prior)

    def _apply_support_prior(self, spectrum, support_prior):
        strength = self.SUPPORT_PRIOR_STRENGTH
        if strength <= 0 or not support_prior:
            return spectrum
        keep = 1.0 - strength
        return [value * (keep + strength * support_prior[index]) for index, value in enumerate(spectrum)]

    def _predict_channels(self, spectrum, rows):
        prediction = []
        for item in rows:
            start, weights = item
            total = 0.0
            for offset, weight in enumerate(weights):
                total += weight * spectrum[start + offset]
            prediction.append(total)
        return prediction

    def _smooth_spectrum(self, spectrum):
        if len(spectrum) < 3:
            return spectrum
        radius = int(round(self.SMOOTH_RADIUS_NM / max(self.GRID_STEP_NM, 0.1)))
        if radius <= 1:
            out = [0.0] * len(spectrum)
            out[0] = spectrum[0]
            for index in range(1, len(spectrum) - 1):
                out[index] = 0.22 * spectrum[index - 1] + 0.56 * spectrum[index] + 0.22 * spectrum[index + 1]
            out[-1] = spectrum[-1]
            return out
        prefix = [0.0]
        total = 0.0
        for value in spectrum:
            total += value
            prefix.append(total)
        out = [0.0] * len(spectrum)
        blend = self.SMOOTH_BLEND
        keep = 1.0 - blend
        last = len(spectrum) - 1
        for index, value in enumerate(spectrum):
            start = max(0, index - radius)
            end = min(last, index + radius)
            avg = (prefix[end + 1] - prefix[start]) / (end - start + 1)
            out[index] = keep * value + blend * avg
        return out

    def _apply_clear_constraint(self, spectrum, clear_value, max_sensitivity, exposure, grid):
        clear_y = max(0.0, float(clear_value)) / exposure
        clear_scale = float(self.CLEAR_BAND[2]) / max_sensitivity
        clear_y = clear_y / max(clear_scale, 0.01)
        if clear_y <= 0:
            return spectrum
        clear_row = self._clear_row(grid)
        predicted = 0.0
        for index, value in enumerate(spectrum):
            predicted += clear_row[index] * value
        if predicted <= 1e-12:
            return spectrum
        factor = clear_y / predicted
        factor = max(0.55, min(1.85, factor))
        return [value * factor for value in spectrum]

    def _clear_row(self, grid):
        cached = AS7341._CLEAR_ROW_CACHE
        if cached and len(cached) == len(grid):
            return cached
        cached = self._response_row(self.CLEAR_BAND[0], self.CLEAR_BAND[1], grid)
        AS7341._CLEAR_ROW_CACHE = cached
        return cached

    def _fit_error(self, prediction, ycorr):
        ref = max(max(ycorr), 1e-12)
        total = 0.0
        for index, value in enumerate(ycorr):
            total += abs(prediction[index] - value) / (abs(value) + ref * 0.05)
        return total / max(1, len(ycorr))

    def _spectrum_summary(self, grid, power, corrected, fit_error, exposure, peak_support=None):
        max_power = max(max(power), 1e-12)
        values = [value / max_power for value in power]
        visible_pairs = [(w, p) for w, p in zip(grid, power) if w <= 780]
        visible_total = sum([p for _, p in visible_pairs])
        if visible_total > 0:
            centroid = sum([w * p for w, p in visible_pairs]) / visible_total
            dominant_nm = 0
            dominant_power = -1.0
            for index, item in enumerate(visible_pairs):
                wavelength, value = item
                if peak_support and index < len(peak_support) and peak_support[index] < self.PEAK_SUPPORT_MIN:
                    continue
                if value > dominant_power:
                    dominant_nm = wavelength
                    dominant_power = value
            if dominant_power < 0:
                dominant_nm = visible_pairs[0][0]
        else:
            centroid = 0
            dominant_nm = 0

        blue = sum([p for w, p in visible_pairs if w < 500])
        green = sum([p for w, p in visible_pairs if w >= 500 and w < 600])
        red = sum([p for w, p in visible_pairs if w >= 600])
        cct = 0
        if blue + green + red > 0:
            cct = int(6500.0 * (blue + 0.35 * green) / max(red + 0.20 * green, 1e-12))
            cct = max(1500, min(15000, cct))

        visible_avg = sum(corrected[:8]) / 8.0 if corrected[:8] else 0.0
        nir_ratio = float(corrected[9]) / max(1.0, visible_avg)
        clear_ratio = float(corrected[8]) / max(1.0, visible_avg)
        confidence = max(0.0, min(1.0, 1.0 - fit_error))
        lux_est, clear_lux_signal = self._estimate_lux_from_spd(grid, power, corrected, exposure)
        return {
            "grid": list(grid),
            "values": values,
            "power": power,
            "dominant_nm": round(float(dominant_nm), 1),
            "centroid_nm": round(float(centroid), 1),
            "lux_est": lux_est,
            "clear_lux_signal": clear_lux_signal,
            "lux_source": "spd_photopic",
            "cct_est": int(cct),
            "nir_ratio": nir_ratio,
            "clear_ratio": clear_ratio,
            "fit_confidence": confidence,
        }

    def _estimate_lux_from_clear(self, corrected, exposure):
        clear_signal = self._clear_lux_signal(corrected, exposure)
        lux_est = clear_signal * self.CLEAR_LUX_CALIBRATION
        return max(0.0, lux_est), clear_signal

    def _clear_lux_signal(self, corrected, exposure):
        clear_value = float(corrected[8]) if len(corrected) > 8 else 0.0
        return max(0.0, clear_value) / max(0.001, float(exposure))

    def _estimate_lux_from_spd(self, grid, power, corrected, exposure):
        clear_signal = self._clear_lux_signal(corrected, exposure)
        weights = self._photopic_weights(grid)
        total = 0.0
        for index, value in enumerate(power):
            if index >= len(weights):
                break
            total += max(0.0, float(value)) * weights[index]
        lux_est = 683.0 * total * self.SPD_LUX_CALIBRATION
        return max(0.0, lux_est), clear_signal

    def _photopic_weights(self, grid):
        cached = AS7341._PHOTOPIC_WEIGHT_CACHE
        if cached and len(cached) == len(grid):
            return cached
        if len(grid) < 2:
            return [0.0 for _ in grid]
        delta_nm = abs(float(grid[1]) - float(grid[0]))
        weights = []
        for wavelength in grid:
            wavelength = float(wavelength)
            if wavelength < 380.0 or wavelength > 780.0:
                weights.append(0.0)
                continue
            sigma = 46.0 if wavelength < 555.0 else 70.0
            v_lambda = math.exp(-0.5 * ((wavelength - 555.0) / sigma) ** 2)
            weights.append(v_lambda * delta_nm)
        AS7341._PHOTOPIC_WEIGHT_CACHE = weights
        return weights

    def _find_spectrum_peaks(self, grid, values, peak_support=None, limit=5):
        candidates = []
        if len(values) < 3:
            return candidates
        for index in range(1, len(values) - 1):
            if peak_support and index < len(peak_support) and peak_support[index] < self.PEAK_SUPPORT_MIN:
                continue
            value = values[index]
            if value >= values[index - 1] and value > values[index + 1] and value >= 0.10:
                candidates.append((value, grid[index], index))
        if not candidates:
            max_value = 0.0
            index = -1
            for item_index, value in enumerate(values):
                if peak_support and item_index < len(peak_support) and peak_support[item_index] < self.PEAK_SUPPORT_MIN:
                    continue
                if value > max_value:
                    max_value = value
                    index = item_index
            if index >= 0 and max_value > 0:
                candidates.append((values[index], grid[index], index))
        candidates.sort(reverse=True)
        selected = []
        for value, wavelength, index in candidates:
            too_close = False
            for item in selected:
                if abs(wavelength - item["wavelength"]) < 35:
                    too_close = True
                    break
            if too_close:
                continue
            selected.append({"wavelength": round(float(wavelength), 1), "value": float(value), "index": int(index)})
            if len(selected) >= limit:
                break
        selected.sort(key=lambda item: item["wavelength"])
        return selected

    def _reconstruct_ir(self, corrected, exposure):
        grid = self.IR_GRID
        _, _, sensitivity = self.NIR_BAND
        nir_signal = max(0.0, float(corrected[9])) / max(0.001, exposure)
        sensitivity_scale = max(0.01, sensitivity / 1350.0)
        amplitude = nir_signal / sensitivity_scale
        shape = self._ir_shape()
        values = [amplitude * value for value in shape["shape"]]
        if amplitude <= 1e-12:
            normalized = [0.0 for _ in grid]
            peak_nm = 0
        else:
            normalized = shape["normalized"]
            peak_nm = shape["peak_nm"]
        visible_avg = sum(corrected[:8]) / 8.0 if corrected[:8] else 0.0
        relative = float(corrected[9]) / max(1.0, visible_avg)
        return {
            "grid": list(grid),
            "values": normalized,
            "power": values,
            "peak_nm": peak_nm,
            "relative": relative,
        }

    def _ir_shape(self):
        cached = AS7341._IR_SHAPE_CACHE
        if cached:
            return cached
        grid = self.IR_GRID
        center, fwhm, _ = self.NIR_BAND
        sigma = max(1.0, float(fwhm) / 2.355)
        shape = []
        for wavelength in grid:
            shape.append(math.exp(-0.5 * ((float(wavelength) - center) / sigma) ** 2))
        max_raw = max(shape) if shape else 0.0
        if max_raw <= 1e-12:
            normalized = [0.0 for _ in grid]
            peak_nm = 0
        else:
            normalized = [value / max_raw for value in shape]
            peak_index = normalized.index(max(normalized))
            peak_nm = round(float(grid[peak_index]), 1) if grid else 0
        cached = {"shape": shape, "normalized": normalized, "peak_nm": peak_nm}
        AS7341._IR_SHAPE_CACHE = cached
        return cached

    def set_dark_from_sample(self, sample):
        self.dark = list(sample["raw"])

    def clear_dark(self):
        self.dark = [0] * len(self.CHANNELS)

    def gain_up(self):
        self.set_gain_index(self.gain_index + 1)

    def gain_down(self):
        self.set_gain_index(self.gain_index - 1)

    def integration_up(self):
        steps = [10, 25, 50, 100, 200, 400, 800]
        current = self.integration_ms()
        for value in steps:
            if value > current + 1:
                self.set_integration(value)
                return
        self.set_integration(steps[-1])

    def integration_down(self):
        steps = [10, 25, 50, 100, 200, 400, 800]
        current = self.integration_ms()
        for value in reversed(steps):
            if value < current - 1:
                self.set_integration(value)
                return
        self.set_integration(steps[0])

# ===== spectrometer_ui.py =====
try:
    from maix import image
except Exception:
    image = None


class Palette:
    def __init__(self):
        self.bg = self.rgb(10, 14, 20)
        self.panel = self.rgb(18, 24, 34)
        self.panel2 = self.rgb(24, 32, 44)
        self.line = self.rgb(49, 63, 82)
        self.text = self.rgb(232, 238, 244)
        self.muted = self.rgb(140, 152, 166)
        self.dim = self.rgb(92, 105, 122)
        self.accent = self.rgb(38, 190, 170)
        self.warn = self.rgb(255, 176, 76)
        self.danger = self.rgb(238, 93, 93)
        self.good = self.rgb(94, 214, 132)
        self.blue = self.rgb(88, 166, 255)
        self.violet = self.rgb(174, 128, 255)

    def rgb(self, r, g, b):
        if image is None:
            return (r, g, b)
        return image.Color.from_rgb(r, g, b)


class SpectrometerUI:
    CHANNEL_COLORS = [
        (110, 84, 255),
        (74, 121, 255),
        (49, 169, 255),
        (43, 208, 190),
        (118, 218, 94),
        (229, 205, 76),
        (255, 146, 64),
        (255, 84, 76),
        (210, 219, 228),
        (178, 130, 255),
    ]

    def __init__(self, width, height):
        if image is None:
            raise RuntimeError("This module must run inside MaixPy")
        self.w = width
        self.h = height
        self.p = Palette()
        self.buttons = []
        self.last_pressed = False
        self.last_action = None
        self.history = []
        self.max_history = 80
        self.show_ir = False

    def make_image(self):
        return image.Image(self.w, self.h, image.Format.FMT_RGB888, self.p.bg)

    def draw(self, sample, sensor, running=True, message=""):
        img = self.make_image()
        self.buttons = []
        self._draw_top_bar(img, sample, sensor, running, message)
        graph = self._layout_graph()
        table = self._layout_table()
        self._draw_graph(img, graph, sample)
        self._draw_cards(img, table, sample)
        self._draw_bottom_controls(img, running)
        return img

    def handle_touch(self, x, y, pressed):
        action = None
        if pressed and not self.last_pressed:
            for button in self.buttons:
                bx, by, bw, bh, name = button
                if x >= bx and x <= bx + bw and y >= by and y <= by + bh:
                    action = name
                    break
        if action == "toggle_ir":
            self.show_ir = not self.show_ir
            action = None
        self.last_pressed = bool(pressed)
        self.last_action = action
        return action

    def add_history(self, sample):
        if not sample:
            return
        visible = sample.get("corrected", [])[:8]
        if not visible:
            return
        self.history.append(max(visible))
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def _layout_graph(self):
        top = 66
        bottom_controls = 74
        left = 18
        width = int(self.w * 0.64) - 24
        height = self.h - top - bottom_controls - 12
        return (left, top, width, height)

    def _layout_table(self):
        graph = self._layout_graph()
        left = graph[0] + graph[2] + 14
        top = graph[1]
        width = self.w - left - 18
        height = graph[3]
        return (left, top, width, height)

    def _draw_top_bar(self, img, sample, sensor, running, message):
        img.draw_rect(0, 0, self.w, 58, self.p.panel, -1)
        img.draw_line(0, 57, self.w, 57, self.p.line, 1)
        img.draw_string(18, 12, "AS7341 Spectrometer", self.p.text, 1.35)
        state = "RUN" if running else "PAUSE"
        state_color = self.p.good if running else self.p.warn
        self._pill(img, self.w - 104, 12, 82, 28, state, state_color)

        if sample:
            peak = sample.get("peak", {})
            info = "Peak %s %snm   Gain %.1fx   %.0fms" % (
                peak.get("name", "--"),
                peak.get("wavelength", 0),
                sample.get("gain", 0),
                sample.get("integration_ms", 0),
            )
            spectrum = sample.get("spectrum", {})
            if spectrum:
                info = "SPD peak %s   centroid %s   Lux~%.0f" % (
                    self._format_nm(spectrum.get("dominant_nm", 0)),
                    self._format_nm(spectrum.get("centroid_nm", 0)),
                    spectrum.get("lux_est", 0),
                )
            if sample.get("saturated"):
                info += "   SAT"
            color = self.p.warn if sample.get("saturated") else self.p.muted
            img.draw_string(18, 38, info, color, 0.78)
        else:
            img.draw_string(18, 38, "Waiting for sensor data", self.p.muted, 0.78)
        if sensor and hasattr(sensor, "web_label"):
            img.draw_string(max(310, int(self.w * 0.60) - 80), 17, sensor.web_label, self.p.dim, 0.9, wrap=False)
        if message:
            img.draw_string(max(390, int(self.w * 0.60)), 38, message[:22], self.p.warn, 0.78)

    def _draw_graph(self, img, rect, sample):
        x, y, w, h = rect
        self._panel(img, x, y, w, h)
        img.draw_string(x + 16, y + 12, "Reconstructed SPD", self.p.text, 1.05)
        img.draw_string(x + w - 156, y + 15, "relative 380-780nm", self.p.muted, 0.72)

        gx = x + 40
        gy = y + 48
        gw = w - 58
        gh = h - 96
        self._grid(img, gx, gy, gw, gh)

        spectrum = sample.get("spectrum", {}) if sample else {}
        if spectrum and spectrum.get("values"):
            self._draw_spd_curve(img, gx, gy, gw, gh, spectrum)
            self._draw_channel_markers(img, gx, gy, gw, gh, sample)
            self._draw_ir_window(img, gx + gw - 146, gy + 8, 136, 88, spectrum.get("ir", {}))
            peaks = spectrum.get("peaks", [])
            if peaks:
                labels = "Peaks " + " ".join([self._format_nm(p["wavelength"]) for p in peaks[:5]])
                img.draw_string(gx, gy + gh + 30, labels[:54], self.p.text, 0.72, wrap=False)
            fit = "Fit %.0f%%  CCT~%dK  Lux~%.0f" % (
                spectrum.get("fit_confidence", 0) * 100,
                spectrum.get("cct_est", 0),
                spectrum.get("lux_est", 0),
            )
            img.draw_string(gx + gw - 170, gy - 25, fit, self.p.muted, 0.58, wrap=False)
        else:
            img.draw_string(gx + 16, gy + int(gh / 2) - 8, "waiting for reconstructed spectrum", self.p.dim, 0.72)
        self._draw_history(img, x + 18, y + h - 38, w - 36, 20)

    def _draw_spd_curve(self, img, x, y, w, h, spectrum):
        grid = spectrum.get("grid", [])
        values = spectrum.get("values", [])
        if len(grid) < 2 or len(values) != len(grid):
            return
        wl_min = grid[0]
        wl_max = grid[-1]
        span = max(0.1, wl_max - wl_min)
        bucket_values = [-1.0 for _ in range(w + 1)]
        bucket_indices = [-1 for _ in range(w + 1)]
        for idx, wavelength in enumerate(grid):
            offset = int((wavelength - wl_min) * w / span)
            if offset < 0:
                offset = 0
            elif offset > w:
                offset = w
            value = float(values[idx])
            if value > bucket_values[offset]:
                bucket_values[offset] = value
                bucket_indices[offset] = idx
        points = []
        for offset, value in enumerate(bucket_values):
            if value < 0:
                continue
            idx = bucket_indices[offset]
            px = x + offset
            py = y + h - int(value * (h - 8))
            points.append((px, py, idx, value))
        for idx in range(len(points) - 1):
            color = self._wavelength_color(grid[points[idx][2]])
            fill_x1 = points[idx][0]
            fill_x2 = max(fill_x1 + 1, points[idx + 1][0])
            y1 = points[idx][1]
            y2 = points[idx + 1][1]
            for px in range(fill_x1, fill_x2 + 1, 2):
                mix = (px - fill_x1) / max(1, fill_x2 - fill_x1)
                top_y = int(y1 + (y2 - y1) * mix)
                img.draw_line(px, top_y, px, y + h, color, 2)
        for idx in range(len(points) - 1):
            img.draw_line(points[idx][0], points[idx][1], points[idx + 1][0], points[idx + 1][1], self.p.text, 2)
        for tick in [400, 500, 600, 700]:
            px = x + int((tick - wl_min) * w / span)
            img.draw_line(px, y + h - 5, px, y + h + 3, self.p.dim, 1)
            img.draw_string(px - 10, y + h + 10, str(tick), self.p.dim, 0.48, wrap=False)
        for peak in spectrum.get("peaks", [])[:5]:
            wavelength = float(peak.get("wavelength", 0))
            value = float(peak.get("value", 0))
            px = x + int((wavelength - wl_min) * w / span)
            py = y + h - int(value * (h - 8))
            label = self._format_nm(wavelength)
            img.draw_circle(px, py, 5, self.p.text, -1)
            label_x = max(x, min(x + w - 70, px - 30))
            label_y = max(y + 2, py - 28)
            img.draw_rect(label_x - 3, label_y - 2, 74, 24, self.p.panel, -1)
            img.draw_rect(label_x - 3, label_y - 2, 74, 24, self.p.line, 1)
            img.draw_string(label_x, label_y + 3, label, self.p.text, 0.82, wrap=False)
            img.draw_line(px, py + 5, px, y + h, self.p.dim, 1)

    def _draw_ir_window(self, img, x, y, w, h, ir):
        if not self.show_ir:
            tab_w = 54
            tab_h = 26
            img.draw_rect(x, y, tab_w, tab_h, self.p.panel, -1)
            img.draw_rect(x, y, tab_w, tab_h, self.p.violet, 1)
            img.draw_string(x + 8, y + 7, "IR", self.p.violet, 0.66, wrap=False)
            self.buttons.append((x, y, tab_w, tab_h, "toggle_ir"))
            return

        img.draw_rect(x, y, w, h, self.p.panel, -1)
        img.draw_rect(x, y, w, h, self.p.violet, 1)
        img.draw_string(x + 8, y + 6, "IR 760-1000", self.p.text, 0.58, wrap=False)
        img.draw_string(x + w - 28, y + 6, "hide", self.p.dim, 0.42, wrap=False)
        self.buttons.append((x, y, w, h, "toggle_ir"))
        grid = ir.get("grid", [])
        values = ir.get("values", [])
        if len(grid) < 2 or len(values) != len(grid):
            return
        gx = x + 10
        gy = y + 28
        gw = w - 20
        gh = h - 40
        wl_min = grid[0]
        wl_max = grid[-1]
        span = max(0.1, wl_max - wl_min)
        bucket_values = [-1.0 for _ in range(gw + 1)]
        for idx, wavelength in enumerate(grid):
            offset = int((wavelength - wl_min) * gw / span)
            if offset < 0:
                offset = 0
            elif offset > gw:
                offset = gw
            value = float(values[idx])
            if value > bucket_values[offset]:
                bucket_values[offset] = value
        last = None
        for offset, value in enumerate(bucket_values):
            if value < 0:
                continue
            px = gx + offset
            py = gy + gh - int(value * (gh - 4))
            img.draw_line(px, py, px, gy + gh, self.p.violet, 2)
            if last:
                img.draw_line(last[0], last[1], px, py, self.p.text, 1)
            last = (px, py)
        peak_nm = ir.get("peak_nm", 0)
        rel = ir.get("relative", 0)
        img.draw_string(x + 10, y + h - 12, "%s  %.2fx" % (self._format_nm(peak_nm), rel), self.p.muted, 0.48, wrap=False)

    def _draw_channel_markers(self, img, x, y, w, h, sample):
        values = sample.get("corrected", [0] * 10)
        max_value = max(max(values[:8]), 1)
        wavelengths = [415, 445, 480, 515, 555, 590, 630, 680]
        channel_values = values[:8]
        wl_min = 380
        wl_max = 780
        for idx, wavelength in enumerate(wavelengths):
            px = x + int((wavelength - wl_min) * w / (wl_max - wl_min))
            marker_h = int((channel_values[idx] / max_value) * 26)
            color = self._channel_color(idx)
            img.draw_rect(px - 2, y + h - marker_h, 4, marker_h, color, -1)

    def _wavelength_color(self, wavelength):
        if wavelength < 430:
            return self.p.rgb(118, 92, 255)
        if wavelength < 480:
            return self.p.rgb(79, 130, 255)
        if wavelength < 520:
            return self.p.rgb(51, 183, 238)
        if wavelength < 575:
            return self.p.rgb(74, 218, 123)
        if wavelength < 610:
            return self.p.rgb(235, 213, 73)
        if wavelength < 660:
            return self.p.rgb(255, 150, 63)
        if wavelength < 760:
            return self.p.rgb(255, 85, 78)
        return self.p.rgb(188, 130, 255)

    def _format_nm(self, value):
        try:
            value = float(value)
        except Exception:
            value = 0.0
        if value <= 0:
            return "0nm"
        return "%.1fnm" % value

    def _draw_cards(self, img, rect, sample):
        x, y, w, h = rect
        self._panel(img, x, y, w, h)
        img.draw_string(x + 14, y + 12, "Channels", self.p.text, 1.0)
        values = sample.get("corrected", [0] * 10) if sample else [0] * 10
        raw = sample.get("raw", [0] * 10) if sample else [0] * 10
        channels = sample.get("channels", []) if sample else []
        if not channels:
            labels = ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "Clear", "NIR"]
            wavelengths = [415, 445, 480, 515, 555, 590, 630, 680, 0, 910]
            channels = list(zip(labels, wavelengths))

        row_h = max(29, int((h - 58) / 10))
        start_y = y + 45
        for idx, (name, wavelength) in enumerate(channels[:10]):
            ry = start_y + idx * row_h
            color = self._channel_color(idx)
            img.draw_rect(x + 12, ry, 5, row_h - 7, color, -1)
            label = "%s" % name
            if wavelength:
                label += " %dnm" % wavelength
            img.draw_string(x + 24, ry + 2, label, self.p.text, 0.66, wrap=False)
            img.draw_string(x + w - 92, ry + 1, "%5d" % values[idx], color, 0.68, wrap=False)
            img.draw_string(x + w - 92, ry + 15, "raw %5d" % raw[idx], self.p.dim, 0.48, wrap=False)

    def _draw_bottom_controls(self, img, running):
        y = self.h - 62
        img.draw_rect(0, y - 8, self.w, 70, self.p.panel, -1)
        img.draw_line(0, y - 8, self.w, y - 8, self.p.line, 1)
        specs = [
            ("pause" if running else "run", "Pause" if running else "Run", self.p.accent),
            ("zero", "Zero", self.p.blue),
            ("gain_down", "Gain -", self.p.panel2),
            ("gain_up", "Gain +", self.p.panel2),
            ("int_down", "Int -", self.p.panel2),
            ("int_up", "Int +", self.p.panel2),
            ("save", "Save", self.p.good),
            ("exit", "Exit", self.p.danger),
        ]
        margin = 12
        gap = 8
        bw = int((self.w - margin * 2 - gap * (len(specs) - 1)) / len(specs))
        for idx, (name, label, color) in enumerate(specs):
            bx = margin + idx * (bw + gap)
            self._button(img, bx, y, bw, 42, label, color)
            self.buttons.append((bx, y, bw, 42, name))

    def _draw_history(self, img, x, y, w, h):
        img.draw_rect(x, y, w, h, self.p.panel2, -1)
        if len(self.history) < 2:
            img.draw_string(x + 6, y + 4, "history", self.p.dim, 0.48)
            return
        max_v = max(max(self.history), 1)
        step = w / max(1, len(self.history) - 1)
        last = None
        for idx, value in enumerate(self.history):
            px = int(x + idx * step)
            py = int(y + h - 2 - (value / max_v) * (h - 4))
            if last:
                img.draw_line(last[0], last[1], px, py, self.p.accent, 1)
            last = (px, py)

    def _grid(self, img, x, y, w, h):
        img.draw_rect(x, y, w, h, self.p.panel2, -1)
        for idx in range(5):
            gy = y + int(idx * h / 4)
            img.draw_line(x, gy, x + w, gy, self.p.line, 1)
        for idx in range(9):
            gx = x + int(idx * w / 8)
            img.draw_line(gx, y, gx, y + h, self.p.line, 1)

    def _panel(self, img, x, y, w, h):
        img.draw_rect(x, y, w, h, self.p.panel, -1)
        img.draw_rect(x, y, w, h, self.p.line, 1)

    def _button(self, img, x, y, w, h, label, color):
        img.draw_rect(x, y, w, h, color, -1)
        img.draw_rect(x, y, w, h, self.p.text, 1)
        tx = x + max(8, int((w - len(label) * 8) / 2))
        img.draw_string(tx, y + 13, label, self.p.text, 0.72, wrap=False)

    def _pill(self, img, x, y, w, h, label, color):
        img.draw_rect(x, y, w, h, color, -1)
        img.draw_string(x + 19, y + 8, label, self.p.bg, 0.72, wrap=False)

    def _channel_color(self, idx):
        r, g, b = self.CHANNEL_COLORS[idx % len(self.CHANNEL_COLORS)]
        return self.p.rgb(r, g, b)

# ===== as7341_spectrometer_maixcam2.py =====

try:
    from maix import app, display, image, time
except Exception:
    app = None
    display = None
    image = None
    time = None


CSV_PATH = "as7341_spectrum_long.csv"
WEB_PORT = 2932


class WebServer:
    def __init__(self, owner, port=WEB_PORT):
        self.owner = owner
        self.port = port
        self.sock = None
        self.enabled = False
        self.host = "0.0.0.0"
        self.label = "WebUI 0.0.0.0:%d" % self.port
        self.start()

    def start(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            self.sock.bind(("0.0.0.0", self.port))
            self.sock.listen(2)
            self.sock.setblocking(False)
            self.enabled = True
            self.host = self._detect_ip()
            self.label = "WebUI %s:%d" % (self.host, self.port)
        except Exception as exc:
            self.enabled = False
            self.sock = None
            self.owner.message = "web off: %s" % str(exc)[:24]

    def _detect_ip(self):
        probe = None
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "0.0.0.0"
        finally:
            try:
                if probe:
                    probe.close()
            except Exception:
                pass

    def poll(self):
        if not self.enabled or self.sock is None:
            return
        try:
            client, _ = self.sock.accept()
        except Exception:
            return
        try:
            client.settimeout(0.25)
            request = client.recv(1024)
            if not request:
                client.close()
                return
            line = request.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
            parts = line.split(" ")
            path = parts[1] if len(parts) > 1 else "/"
            self._route(client, path)
        except Exception:
            try:
                client.close()
            except Exception:
                pass

    def _route(self, client, path):
        if path == "/" or path.startswith("/?"):
            self._send(client, self._html(), "text/html; charset=utf-8")
        elif path.startswith("/api/state"):
            self._send(client, self._json_state(), "application/json")
        elif path.startswith("/api/action"):
            name = self._query_value(path, "name")
            self.owner.handle_web_action(name)
            self._send(client, self._json_state(), "application/json")
        elif path.startswith("/download.csv"):
            self._send_file(client, CSV_PATH, "text/csv; charset=utf-8")
        else:
            self._send(client, "Not Found", "text/plain; charset=utf-8", "404 Not Found")

    def _query_value(self, path, key):
        marker = key + "="
        if "?" not in path or marker not in path:
            return ""
        query = path.split("?", 1)[1]
        for part in query.split("&"):
            if part.startswith(marker):
                return part[len(marker):].replace("%20", " ")
        return ""

    def _send(self, client, body, content_type, status="200 OK"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        header = (
            "HTTP/1.1 %s\r\n"
            "Content-Type: %s\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n"
        ) % (status, content_type, len(body))
        self._send_all(client, header.encode("utf-8"))
        self._send_all(client, body)
        client.close()

    def _send_file(self, client, path, content_type):
        try:
            with open(path, "rb") as fp:
                body = fp.read()
        except Exception:
            body = self.owner.csv_text().encode("utf-8")
        header = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: %s\r\n"
            "Content-Disposition: attachment; filename=\"as7341_spectrum_long.csv\"\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n\r\n"
        ) % (content_type, len(body))
        self._send_all(client, header.encode("utf-8"))
        self._send_all(client, body)
        client.close()

    def _send_all(self, client, data):
        offset = 0
        length = len(data)
        while offset < length:
            sent = client.send(data[offset:offset + 1024])
            if not sent:
                break
            offset += sent

    def _json_state(self):
        data = self.owner.web_state()
        if json:
            return json.dumps(data)
        return self._manual_json(data)

    def _manual_json(self, value):
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(value, list):
            return "[" + ",".join([self._manual_json(v) for v in value]) + "]"
        if isinstance(value, dict):
            items = []
            for key in value:
                items.append(self._manual_json(str(key)) + ":" + self._manual_json(value[key]))
            return "{" + ",".join(items) + "}"
        return self._manual_json(str(value))

    def _html(self):
        return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AS7341 Spectrometer</title>
<style>
body{margin:0;background:#0a0e14;color:#e8eef4;font-family:Arial,Helvetica,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
.top{display:flex;justify-content:space-between;gap:12px;align-items:center}
h1{font-size:24px;margin:0}.status{color:#8c98a6;font-size:14px}
.panel{background:#121822;border:1px solid #313f52;padding:14px;margin-top:14px}
button,a.btn{background:#18202c;border:1px solid #5f6f82;color:#e8eef4;padding:10px 12px;margin:4px;text-decoration:none;display:inline-block}
button.primary{background:#26beaa;color:#07100f}.good{background:#5ed684;color:#07100f}.danger{background:#ee5d5d}
canvas{width:100%;height:360px;background:#18202c;display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}.card{background:#18202c;border:1px solid #313f52;padding:8px}
.muted{color:#8c98a6}.small{font-size:12px}
</style>
</head>
<body><div class="wrap">
<div class="top"><div><h1>AS7341 Spectrometer</h1><div id="status" class="status">connecting</div></div><a class="btn" href="/download.csv">Download CSV</a></div>
<div class="panel"><canvas id="plot" width="1000" height="360"></canvas><div id="stats" class="status"></div></div>
<div class="panel">
<button id="run" class="primary">Run/Pause</button><button onclick="act('zero')">Zero</button>
<button onclick="act('gain_down')">Gain -</button><button onclick="act('gain_up')">Gain +</button>
<button onclick="act('int_down')">Int -</button><button onclick="act('int_up')">Int +</button>
<button onclick="act('save')" class="good">Save</button><button onclick="act('exit')" class="danger">Exit</button>
</div>
<div class="panel"><div id="channels" class="grid"></div></div>
</div>
<script>
let last=null;
function act(name){fetch('/api/action?name='+name).then(r=>r.json()).then(draw).catch(()=>{});}
document.getElementById('run').onclick=()=>act(last&&last.running?'pause':'run');
function colorFor(w){if(w<430)return'#765cff';if(w<480)return'#4f82ff';if(w<520)return'#33b7ee';if(w<575)return'#4ada7b';if(w<610)return'#ebd549';if(w<660)return'#ff963f';return'#ff554e'}
function draw(data){
 last=data; document.getElementById('status').textContent=(data.running?'RUN':'PAUSE')+' | '+data.message+' | gain '+data.gain+'x | '+data.integration_ms.toFixed(0)+'ms';
 const c=document.getElementById('plot'),ctx=c.getContext('2d'),W=c.width,H=c.height;ctx.clearRect(0,0,W,H);ctx.fillStyle='#18202c';ctx.fillRect(0,0,W,H);
 ctx.strokeStyle='#313f52';ctx.lineWidth=1;for(let i=0;i<5;i++){let y=30+i*(H-70)/4;ctx.beginPath();ctx.moveTo(50,y);ctx.lineTo(W-25,y);ctx.stroke();}
 let g=data.spectrum.grid||[],v=data.spectrum.values||[]; if(g.length>1){let x0=50,y0=H-40,gw=W-80,gh=H-80;for(let i=0;i<g.length-1;i++){let x1=x0+(g[i]-380)*gw/400,x2=x0+(g[i+1]-380)*gw/400,y1=y0-v[i]*gh,y2=y0-v[i+1]*gh;ctx.fillStyle=colorFor(g[i]);ctx.beginPath();ctx.moveTo(x1,y0);ctx.lineTo(x1,y1);ctx.lineTo(x2,y2);ctx.lineTo(x2,y0);ctx.closePath();ctx.fill();}ctx.strokeStyle='#e8eef4';ctx.lineWidth=2;ctx.beginPath();for(let i=0;i<g.length;i++){let x=x0+(g[i]-380)*gw/400,y=y0-v[i]*gh;if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);}ctx.stroke();ctx.fillStyle='#e8eef4';ctx.font='18px Arial';(data.spectrum.peaks||[]).slice(0,5).forEach(p=>{let x=x0+(p.wavelength-380)*gw/400,y=y0-p.value*gh;ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fill();ctx.fillText(Number(p.wavelength).toFixed(1)+'nm',Math.max(52,Math.min(W-96,x-32)),Math.max(22,y-12));});}
 document.getElementById('stats').textContent='Peak '+Number(data.spectrum.dominant_nm||0).toFixed(1)+'nm | centroid '+Number(data.spectrum.centroid_nm||0).toFixed(1)+'nm | Lux~'+Number(data.spectrum.lux_est||0).toFixed(0)+' | fit '+Math.round(data.spectrum.fit_confidence*100)+'% | CCT~'+data.spectrum.cct_est+'K | IR '+(data.spectrum.ir?data.spectrum.ir.relative.toFixed(2):'0');
 const ch=document.getElementById('channels');ch.innerHTML='';(data.channels||[]).forEach((it,i)=>{let d=document.createElement('div');d.className='card';d.innerHTML='<b>'+it.name+(it.wavelength?' '+it.wavelength+'nm':'')+'</b><br><span>'+it.corrected+'</span><br><span class="muted small">raw '+it.raw+'</span>';ch.appendChild(d);});
}
function tick(){fetch('/api/state').then(r=>r.json()).then(draw).catch(()=>{});}setInterval(tick,700);tick();
</script></body></html>"""


class SpectrometerApp:
    def __init__(self):
        if app is None or display is None or image is None or time is None:
            raise RuntimeError("Run this program with MaixPy on MaixCAM2")
        from maix import touchscreen

        self.disp = display.Display()
        self.ts = touchscreen.TouchScreen()
        self.ui = SpectrometerUI(self.disp.width(), self.disp.height())
        self.bus = None
        self.sensor = None
        self.running = True
        self.message = "starting"
        self.sample = None
        self.last_sample_ms = 0
        self.last_draw_ms = 0
        self.last_save_ms = 0
        self.retry_after_ms = 0
        self.sample_interval_ms = 60
        self.csv_header_written = False
        self.web = WebServer(self, WEB_PORT)
        self.web_label = self.web.label

    def run(self):
        self._connect_sensor()
        while not app.need_exit():
            self.web.poll()
            now = time.ticks_ms()
            action = self._read_touch_action()
            if action:
                self._handle_action(action)

            if self.sensor is None:
                if now >= self.retry_after_ms:
                    self._connect_sensor()
                self.web.poll()
                self._draw()
                time.sleep_ms(40)
                continue

            if self.running and now - self.last_sample_ms >= self.sample_interval_ms:
                self._read_sample()
                self.last_sample_ms = now

            if now - self.last_draw_ms >= 40:
                self._draw()
                self.last_draw_ms = now
            self.web.poll()
            time.sleep_ms(4)

    def _connect_sensor(self):
        try:
            self.message = "probing AS7341"
            self.bus = SoftI2C("B20", "B19", freq=80000)
            self.sensor = AS7341(self.bus)
            self.sensor.web_label = self.web_label
            self.sensor.begin()
            self.sample_interval_ms = max(60, int(self.sensor.integration_ms() * 2.5))
            self.message = "ready"
        except (SoftI2CError, AS7341Error, Exception) as exc:
            self.sensor = None
            self.message = "sensor error: %s" % str(exc)[:42]
            self.retry_after_ms = time.ticks_ms() + 1200

    def _read_sample(self):
        try:
            self.sample = self.sensor.read_raw()
            self.sample["channels"] = AS7341.CHANNELS
            self.ui.add_history(self.sample)
            if self.sample.get("saturated"):
                self.message = "saturation: reduce gain/int"
            else:
                self.message = "ready"
        except Exception as exc:
            self.message = "read error: %s" % str(exc)[:42]
            self.sensor = None
            self.retry_after_ms = time.ticks_ms() + 1000

    def _draw(self):
        if self.sensor is None:
            img = self._error_image()
        else:
            img = self.ui.draw(self.sample, self.sensor, self.running, self.message)
        self.disp.show(img)

    def _error_image(self):
        w = self.disp.width()
        h = self.disp.height()
        bg = image.Color.from_rgb(10, 14, 20)
        panel = image.Color.from_rgb(18, 24, 34)
        line = image.Color.from_rgb(49, 63, 82)
        text = image.Color.from_rgb(232, 238, 244)
        muted = image.Color.from_rgb(140, 152, 166)
        warn = image.Color.from_rgb(255, 176, 76)
        img = image.Image(w, h, image.Format.FMT_RGB888, bg)
        box_w = min(w - 48, 540)
        box_h = 230
        x = int((w - box_w) / 2)
        y = int((h - box_h) / 2)
        img.draw_rect(x, y, box_w, box_h, panel, -1)
        img.draw_rect(x, y, box_w, box_h, line, 1)
        img.draw_string(x + 22, y + 24, "AS7341 Spectrometer", text, 1.3)
        img.draw_string(x + 22, y + 66, self.message, warn, 0.78)
        img.draw_string(x + 22, y + 106, "Check wiring: 3V3/GND, SCL=B20, SDA=B19", muted, 0.72)
        img.draw_string(x + 22, y + 132, "AS7341 I2C address must be 0x39.", muted, 0.72)
        img.draw_string(x + 22, y + 166, "The app will retry automatically.", muted, 0.72)
        self.ui.buttons = []
        self.ui._button(img, x + box_w - 116, y + box_h - 58, 88, 38, "Exit", warn)
        self.ui.buttons.append((x + box_w - 116, y + box_h - 58, 88, 38, "exit"))
        return img

    def _read_touch_action(self):
        try:
            x, y, pressed = self.ts.read()
            return self.ui.handle_touch(x, y, pressed)
        except Exception:
            return None

    def _handle_action(self, action):
        if action == "exit":
            app.set_exit_flag(True)
            return
        if self.sensor is None:
            return
        if action == "pause":
            self.running = False
            self.message = "paused"
        elif action == "run":
            self.running = True
            self.message = "running"
        elif action == "zero":
            self._zero_dark()
        elif action == "gain_down":
            self._change_gain(-1)
        elif action == "gain_up":
            self._change_gain(1)
        elif action == "int_down":
            self._change_integration(-1)
        elif action == "int_up":
            self._change_integration(1)
        elif action == "save":
            self._save_sample()

    def handle_web_action(self, action):
        allowed = (
            "run",
            "pause",
            "zero",
            "gain_down",
            "gain_up",
            "int_down",
            "int_up",
            "save",
            "exit",
        )
        if action in allowed:
            self._handle_action(action)

    def web_state(self):
        spectrum = {}
        channels = []
        if self.sample:
            spectrum = self._web_spectrum(self.sample.get("spectrum", {}))
            raw = self.sample.get("raw", [])
            corrected = self.sample.get("corrected", [])
            for index, item in enumerate(AS7341.CHANNELS):
                name, wavelength = item
                channels.append(
                    {
                        "name": name,
                        "wavelength": wavelength,
                        "raw": raw[index] if index < len(raw) else 0,
                        "corrected": corrected[index] if index < len(corrected) else 0,
                    }
                )
        if not spectrum:
            spectrum = {
                "grid": [],
                "values": [],
                "peaks": [],
                "dominant_nm": 0,
                "centroid_nm": 0,
                "lux_est": 0,
                "clear_lux_signal": 0,
                "lux_source": "spd_photopic",
                "cct_est": 0,
                "fit_confidence": 0,
                "ir": {"relative": 0, "peak_nm": 0, "grid": [], "values": []},
            }
        return {
            "running": self.running,
            "message": self.message,
            "connected": self.sensor is not None,
            "gain": self.sample.get("gain", 0) if self.sample else 0,
            "integration_ms": self.sample.get("integration_ms", 0) if self.sample else 0,
            "saturated": self.sample.get("saturated", False) if self.sample else False,
            "spectrum": spectrum,
            "channels": channels,
        }

    def _web_spectrum(self, spectrum):
        if not spectrum:
            return {}
        ir = spectrum.get("ir", {})
        grid, values = self._downsample_curve(spectrum.get("grid", []), spectrum.get("values", []), 900)
        ir_grid, ir_values = self._downsample_curve(ir.get("grid", []), ir.get("values", []), 360)
        return {
            "grid": grid,
            "values": values,
            "dominant_nm": spectrum.get("dominant_nm", 0),
            "centroid_nm": spectrum.get("centroid_nm", 0),
            "lux_est": spectrum.get("lux_est", 0),
            "clear_lux_signal": spectrum.get("clear_lux_signal", 0),
            "lux_source": spectrum.get("lux_source", "spd_photopic"),
            "cct_est": spectrum.get("cct_est", 0),
            "nir_ratio": spectrum.get("nir_ratio", 0),
            "clear_ratio": spectrum.get("clear_ratio", 0),
            "fit_confidence": spectrum.get("fit_confidence", 0),
            "peaks": spectrum.get("peaks", []),
            "ir": {
                "grid": ir_grid,
                "values": ir_values,
                "peak_nm": ir.get("peak_nm", 0),
                "relative": ir.get("relative", 0),
            },
        }

    def _downsample_curve(self, grid, values, target):
        limit = min(len(grid), len(values))
        if limit <= 0:
            return [], []
        if limit <= target:
            return list(grid[:limit]), list(values[:limit])
        bucket = float(limit) / float(target)
        out_grid = []
        out_values = []
        last_index = -1
        for item in range(target):
            start = int(item * bucket)
            end = int((item + 1) * bucket)
            if end <= start:
                end = start + 1
            if end > limit:
                end = limit
            best = start
            best_value = values[start]
            for index in range(start + 1, end):
                if values[index] > best_value:
                    best = index
                    best_value = values[index]
            if best == last_index:
                continue
            out_grid.append(grid[best])
            out_values.append(best_value)
            last_index = best
        if out_grid and out_grid[-1] != grid[limit - 1]:
            out_grid.append(grid[limit - 1])
            out_values.append(values[limit - 1])
        return out_grid, out_values

    def csv_text(self):
        lines = [self._csv_header()]
        if self.sample:
            for row in self._csv_rows(time.ticks_ms(), self.sample):
                lines.append(row)
        return "\n".join(lines) + "\n"

    def _zero_dark(self):
        if not self.sample:
            self._read_sample()
        if self.sample:
            self.sensor.set_dark_from_sample(self.sample)
            self.message = "dark baseline captured"
            self._read_sample()

    def _change_gain(self, delta):
        try:
            if delta > 0:
                self.sensor.gain_up()
            else:
                self.sensor.gain_down()
            self.message = "gain %.1fx" % self.sensor.gain_value()
            self._read_sample()
        except Exception as exc:
            self.message = "gain error: %s" % str(exc)[:36]

    def _change_integration(self, delta):
        try:
            if delta > 0:
                self.sensor.integration_up()
            else:
                self.sensor.integration_down()
            self.sample_interval_ms = max(60, int(self.sensor.integration_ms() * 2.5))
            self.message = "integration %.0fms" % self.sensor.integration_ms()
            self._read_sample()
        except Exception as exc:
            self.message = "integration error: %s" % str(exc)[:30]

    def _save_sample(self):
        if not self.sample:
            self.message = "no sample to save"
            return
        now = time.ticks_ms()
        if now - self.last_save_ms < 500:
            return
        self.last_save_ms = now
        try:
            need_header = self._needs_header()
            with open(CSV_PATH, "a") as fp:
                if need_header:
                    fp.write(self._csv_header() + "\n")
                for row in self._csv_rows(now, self.sample):
                    fp.write(row + "\n")
            self.message = "saved to %s" % CSV_PATH
        except Exception as exc:
            self.message = "save error: %s" % str(exc)[:40]

    def _needs_header(self):
        if self.csv_header_written:
            return False
        try:
            with open(CSV_PATH, "r") as fp:
                first = fp.readline()
            self.csv_header_written = first.strip() == self._csv_header()
            return not self.csv_header_written
        except Exception:
            self.csv_header_written = True
            return True

    def _csv_header(self):
        names = [name for name, _ in AS7341.CHANNELS]
        fields = [
            "ticks_ms",
            "kind",
            "wavelength_nm",
            "relative_intensity",
            "relative_power",
            "lux_est",
            "lux_source",
            "clear_lux_signal",
            "gain",
            "integration_ms",
            "saturated",
            "spd_dominant_nm",
            "spd_centroid_nm",
            "spd_cct_est",
            "spd_fit_confidence",
            "spd_nir_ratio",
            "spd_clear_ratio",
            "spd_peaks_nm",
            "ir_peak_nm",
            "ir_relative",
        ]
        fields += ["raw_" + n for n in names]
        fields += ["dark_" + n for n in names]
        fields += ["corr_" + n for n in names]
        fields += ["norm_" + n for n in names]
        return ",".join(fields)

    def _csv_rows(self, ticks_ms, sample):
        spectrum = sample.get("spectrum", {})
        ir = spectrum.get("ir", {})
        peaks = spectrum.get("peaks", [])
        names = [name for name, _ in AS7341.CHANNELS]
        dark = self.sensor.dark if self.sensor else [0] * len(names)
        base = [
            "%.1f" % sample.get("gain", 0),
            "%.2f" % sample.get("integration_ms", 0),
            "1" if sample.get("saturated") else "0",
            "%.1f" % float(spectrum.get("dominant_nm", 0)),
            "%.1f" % float(spectrum.get("centroid_nm", 0)),
            str(spectrum.get("cct_est", 0)),
            "%.4f" % spectrum.get("fit_confidence", 0),
            "%.4f" % spectrum.get("nir_ratio", 0),
            "%.4f" % spectrum.get("clear_ratio", 0),
            ";".join(["%.1f" % float(p["wavelength"]) for p in peaks[:5]]),
            "%.1f" % float(ir.get("peak_nm", 0)),
            "%.4f" % ir.get("relative", 0),
        ]
        base += [str(int(v)) for v in sample.get("raw", [])]
        base += [str(int(v)) for v in dark]
        base += [str(int(v)) for v in sample.get("corrected", [])]
        base += ["%.5f" % float(v) for v in sample.get("normalized", [])]

        lux_est = "%.3f" % float(spectrum.get("lux_est", 0))
        lux_source = str(spectrum.get("lux_source", "spd_photopic"))
        clear_lux_signal = "%.6f" % float(spectrum.get("clear_lux_signal", 0))
        for row in self._iter_spectrum_csv_rows(
            ticks_ms,
            "visible_spd",
            spectrum.get("grid", []),
            spectrum.get("values", []),
            spectrum.get("power", []),
            lux_est,
            lux_source,
            clear_lux_signal,
            base,
        ):
            yield row
        for row in self._iter_spectrum_csv_rows(
            ticks_ms,
            "ir_spd",
            ir.get("grid", []),
            ir.get("values", []),
            ir.get("power", []),
            lux_est,
            lux_source,
            clear_lux_signal,
            base,
        ):
            yield row

    def _iter_spectrum_csv_rows(self, ticks_ms, kind, grid, values, power, lux_est, lux_source, clear_lux_signal, base):
        limit = min(len(grid), len(values))
        for index in range(limit):
            relative_power = power[index] if index < len(power) else 0.0
            row = [
                str(ticks_ms),
                kind,
                "%.1f" % float(grid[index]),
                "%.8f" % float(values[index]),
                "%.9g" % float(relative_power),
                lux_est,
                lux_source,
                clear_lux_signal,
            ]
            yield ",".join(row + base)


def main():
    SpectrometerApp().run()


if __name__ == "__main__":
    main()
