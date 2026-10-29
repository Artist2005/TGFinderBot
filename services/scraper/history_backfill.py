from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import random


def _round_store_price(raw_price: float, reference_price: float) -> Decimal:
    """
    Округлює ціну до реалістичного магазинного формату як для гривневих цін (1499, 1520),
    так і для валютних цін у USD/EUR/GBP/PLN (19.99, 45.50, 129.00).
    """
    val = max(0.5, raw_price)
    if reference_price >= 5000:
        rounded = round(val / 50.0) * 50.0
    elif reference_price >= 1000:
        rounded = round(val / 10.0) * 10.0
    elif reference_price >= 200:
        rounded = round(val / 5.0) * 5.0
    elif reference_price >= 35:
        rounded = round(val, 0)
    else:
        # Для недорогих товарів у USD/EUR/GBP зберігаємо реалістичну точність до 0.50 / 0.10
        rounded = round(val * 2.0) / 2.0
    return Decimal(str(max(0.5, rounded))).quantize(Decimal("0.01"))


def generate_past_month_history(
    *,
    clean_url: str,
    current_price: Decimal,
    old_price: Decimal | None = None,
    days: int = 30,
    points_count: int = 16,
    now: datetime | None = None,
) -> list[tuple[datetime, Decimal]]:
    """
    Формує реалістичну 30-денну ретроспективу цін до моменту додавання товару.

    Особливості:
    - Не використовує маркетингову «стару ціну без акції» (`old_price`) як стартову точку 30 днів тому.
    - Будує ступінчасті цінові плато (по кілька днів стабільної ціни), як у реальних інтернет-магазинах.
    - 30 днів тому ціна знаходиться поблизу реального ринкового рівня (`current_price` ± 2..5%),
      проходить через 3–4 ринкові періоди протягом місяця і завершується точною `current_price`.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    seed_int = int(hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed_int)

    cur_val = float(current_price)
    step_hours = max(1, int((days * 24) / max(1, points_count - 1)))

    # Якщо магазин зараз надає реальну верифіковану знижку (old_price > current_price),
    # відтворюємо реалістичний перехід від доакційного плато (old_price) до поточної акційної ціни.
    old_val = float(old_price) if old_price is not None and old_price > 0 else 0.0
    if old_val > cur_val * 1.015:
        effective_old = min(old_val, cur_val * 1.85)
        old_dec = Decimal(str(round(effective_old, 2))).quantize(Decimal("0.01"))
        mid_val = cur_val + (effective_old - cur_val) * rng.uniform(0.45, 0.65)
        slight_dip = effective_old * rng.uniform(0.975, 0.992)

        sale_patterns: list[list[Decimal]] = [
            [
                _round_store_price(slight_dip, effective_old),
                old_dec,
                old_dec,
                _round_store_price(mid_val, cur_val),
                Decimal(str(current_price)).quantize(Decimal("0.01")),
                Decimal(str(current_price)).quantize(Decimal("0.01")),
            ],
            [
                old_dec,
                old_dec,
                _round_store_price(slight_dip, effective_old),
                old_dec,
                _round_store_price(mid_val, cur_val),
                Decimal(str(current_price)).quantize(Decimal("0.01")),
            ],
        ]
        plateau_prices = rng.choice(sale_patterns)
    else:
        # 4 реалістичні шаблони поведінки ціни за 30 днів без активної розпродажної закресленої ціни
        patterns: list[list[float]] = [
            # 1. Стабільна ціна місяць тому -> невелике здорожчання в середині -> повернення до поточної
            [0.00, 0.00, +0.04, +0.04, +0.02, 0.00],
            # 2. Невелика знижка на початку місяця -> рівне плато -> поточна ціна
            [-0.03, -0.03, +0.02, +0.02, +0.01, 0.00],
            # 3. Невеликі хвилі навколо поточної ціни без різкого перекосу
            [+0.02, +0.02, -0.02, -0.02, +0.03, 0.00],
            # 4. Рівний тренд із коротким піком у середині місяця
            [+0.01, +0.01, +0.05, +0.03, -0.01, 0.00],
        ]
        chosen_pattern = rng.choice(patterns)
        fine_tune = rng.uniform(-0.012, 0.012)

        plateau_prices = []
        for idx, rel_shift in enumerate(chosen_pattern):
            if idx == len(chosen_pattern) - 1:
                plateau_prices.append(Decimal(str(current_price)).quantize(Decimal("0.01")))
            else:
                factor = 1.0 + rel_shift + (fine_tune if idx % 2 == 0 else -fine_tune)
                plateau_prices.append(_round_store_price(cur_val * factor, cur_val))

    history: list[tuple[datetime, Decimal]] = []
    for idx in range(points_count - 1):
        progress = idx / max(1, points_count - 1)
        point_time = now - timedelta(hours=step_hours * (points_count - 1 - idx))

        # Визначаємо, до якого цінового плато належить день
        bucket = min(len(plateau_prices) - 1, int(progress * len(plateau_prices)))
        history.append((point_time, plateau_prices[bucket]))

    # Остання точка (зараз) — точна актуальна ціна з магазину
    history.append((now, Decimal(str(current_price)).quantize(Decimal("0.01"))))
    return history
