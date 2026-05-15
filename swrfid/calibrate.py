"""
swrfid.calibrate — Power-byte calibration sweep.

The vendor manual's RF-power-to-dBm mapping has a typo and is not
linear in a documented way. This module sweeps power bytes from 0 to
the model maximum and records, at each step, whether a known reference
tag is reliably detected. The result is a per-reader calibration curve
that lets users say "I want ~30 cm range" and get a power byte that
empirically delivers that.

Procedure:
  1. Place a reference tag at the desired test distance.
  2. Call ``sweep(reader, target_epc, ...)``.
  3. Save the returned :class:`CalibrationResult` to JSON for later
     reference, or pass it to :func:`recommended_power` to pick a
     working byte.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .protocol import RF_POWER_MAX, WorkMode
from .reader import RFIDReader


@dataclass
class PowerSample:
    power_byte: int
    attempts: int
    successes: int
    avg_rssi: Optional[float] = None

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0


@dataclass
class CalibrationResult:
    tag_epc: str                   # uppercase hex of the reference tag's EPC
    reader_serial: str             # hex of the reader's SN
    timestamp: str                 # ISO-8601 UTC
    samples: List[PowerSample] = field(default_factory=list)
    model_max: int = 0x1E

    def to_dict(self) -> dict:
        return {
            'tag_epc': self.tag_epc,
            'reader_serial': self.reader_serial,
            'timestamp': self.timestamp,
            'model_max': self.model_max,
            'samples': [
                {
                    'power_byte': s.power_byte,
                    'attempts': s.attempts,
                    'successes': s.successes,
                    'success_rate': round(s.success_rate, 3),
                    'avg_rssi': round(s.avg_rssi, 1) if s.avg_rssi is not None else None,
                }
                for s in self.samples
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def sweep(
    reader: RFIDReader,
    target_epc: bytes,
    *,
    min_power: int = 0x00,
    max_power: Optional[int] = None,
    step: int = 1,
    samples_per_power: int = 5,
    settle: float = 0.1,
    progress: Optional[Callable[[int, int, PowerSample], None]] = None,
) -> CalibrationResult:
    """Sweep RF power and record detection rate for ``target_epc``.

    Parameters:
      reader: an open :class:`RFIDReader`.
      target_epc: the 12-byte EPC of the reference tag.
      min_power, max_power: byte range to sweep. ``max_power`` defaults
        to the largest documented model maximum (0x1E for 5500).
      step: byte increment.
      samples_per_power: inventory cycles per power byte.
      settle: seconds to wait between applying a new power and starting
        inventory; gives the RF stage time to stabilise.
      progress: optional callback ``(current_index, total_count, sample)``
        invoked after each power step — useful for GUI progress bars.
    """
    if max_power is None:
        max_power = max(RF_POWER_MAX.values())
    target_hex = target_epc.hex().upper()
    # Ensure answer mode so inventory() returns cleanly.
    reader.set_work_mode(WorkMode.ANSWER)
    info = reader.system_info()

    result = CalibrationResult(
        tag_epc=target_hex,
        reader_serial=info.serial_hex,
        timestamp=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        model_max=max_power,
    )

    powers = list(range(min_power, max_power + 1, step))
    for i, p in enumerate(powers):
        reader.set_rf_power(p)
        time.sleep(settle)
        successes = 0
        rssis: List[int] = []
        for _ in range(samples_per_power):
            tags = reader.inventory()
            for t in tags:
                if t.epc_hex == target_hex:
                    successes += 1
                    rssis.append(t.rssi)
                    break
        avg_rssi = sum(rssis) / len(rssis) if rssis else None
        sample = PowerSample(
            power_byte=p,
            attempts=samples_per_power,
            successes=successes,
            avg_rssi=avg_rssi,
        )
        result.samples.append(sample)
        if progress is not None:
            progress(i + 1, len(powers), sample)
    return result


def recommended_power(result: CalibrationResult, threshold: float = 1.0) -> Optional[int]:
    """Return the lowest power byte at which the success rate reaches
    ``threshold`` (default: 100 %). None if no power achieves it.
    """
    for s in result.samples:
        if s.success_rate >= threshold:
            return s.power_byte
    return None


def first_detectable_power(result: CalibrationResult) -> Optional[int]:
    """Lowest power byte that detected the tag at least once."""
    for s in result.samples:
        if s.successes > 0:
            return s.power_byte
    return None
