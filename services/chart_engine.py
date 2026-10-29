from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
import io
import threading
from typing import Sequence

import matplotlib

# Неінтерактивний бекенд Agg для серверного середовища без GUI
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import fmt_money, t
from database import crud
from database.models import PriceHistory, Product
from services.scraper import format_marketplace_name

# Глобальний лок для потокобезпечної роботи з глобальним станом pyplot у пулі потоків
_MPL_RENDER_LOCK = threading.Lock()


def _fmt_price(val: float, currency: str = "UAH") -> str:
    """Форматує число в лаконічний цінник з урахуванням валюти (₴, $, €, £, zł)."""
    return fmt_money(val, currency)


def _render_price_chart_sync(
    *,
    title: str,
    marketplace: str,
    currency: str,
    timestamps: list[datetime],
    prices: list[float],
    target_price: float | None = None,
    lang: str = "uk",
) -> io.BytesIO:
    """
    Рендерить графік цін за 30 днів у сучасному стилі FinTech / Apple Stocks:
    - Глибока обсидіанова тема (#090D16) без зайвих рамок
    - Багатошарове неонове свічення лінії (glow effect) + плавний вертикальний градієнт під графіком
    - Права шкала цін із символом валюти (₴, $, €, £, zł)
    - Капсульні бейджі для МІН, МАКС та поточної ціни (локалізовані: UK / EN / RU)
    """
    bg_canvas = "#1e1e2e"
    bg_plot = "#181825"
    grid_color = "#313244"
    text_primary = "#F8FAFC"
    text_muted = "#94A3B8"

    plot_times = list(timestamps)
    plot_prices = list(prices)

    min_y = min(plot_prices)
    max_y = max(plot_prices)
    avg_y = sum(plot_prices) / len(plot_prices)
    cur_y = plot_prices[-1]

    # Колір тренду: зелений (якщо ціна вигідна / не вища за середню), інакше насичений червоно-рожевий
    if cur_y <= avg_y:
        accent_color = "#10B981"  # Emerald Green
        glow_color = "#34D399"
        current_dot_color = "#10B981"
    else:
        accent_color = "#F43F5E"  # Rose Red
        glow_color = "#FB7185"
        current_dot_color = "#F43F5E"

    min_badge_color = "#10B981"
    max_badge_color = "#F43F5E"
    avg_line_color = "#94A3B8"
    target_line_color = "#F59E0B"

    with _MPL_RENDER_LOCK:
        # Оптимізований DPI=115: чіткий рендеринг у Telegram при мінімальному споживанні RAM (~35-45 КБ у буфері)
        fig, ax = plt.subplots(figsize=(10.0, 5.2), dpi=115)
        try:
            fig.patch.set_facecolor(bg_canvas)
            ax.set_facecolor(bg_plot)

            spread = max(max_y - min_y, max_y * 0.04, 1.0 if max_y < 100 else 20.0)
            lower_bound = max(0.0, min_y - spread * 0.45)
            upper_bound = max_y + spread * 0.55
            if target_price is not None and target_price > 0:
                lower_bound = min(lower_bound, max(0.0, target_price - spread * 0.25))
            ax.set_ylim(lower_bound, upper_bound)

            # 1. Плавний багатошаровий вертикальний градієнт під кривою ціни
            gradient_steps = 14
            for step in range(1, gradient_steps + 1):
                ratio = step / gradient_steps
                band_y = [lower_bound + (p - lower_bound) * ratio for p in plot_prices]
                ax.fill_between(
                    plot_times,
                    band_y,
                    lower_bound,
                    color=accent_color,
                    alpha=0.02,
                    zorder=2,
                    linewidth=0,
                )

            # 2. Ефект неонового свічення (3 шари лінії) + маркери точок
            ax.plot(plot_times, plot_prices, color=glow_color, linewidth=9.0, alpha=0.07, zorder=3)
            ax.plot(plot_times, plot_prices, color=glow_color, linewidth=5.0, alpha=0.16, zorder=3)
            ax.plot(
                plot_times,
                plot_prices,
                color=accent_color,
                linewidth=2.5,
                marker="o",
                markersize=4.5,
                markerfacecolor=bg_canvas,
                markeredgecolor=accent_color,
                markeredgewidth=1.8,
                zorder=4,
            )

            # 3. Лінія середньої ціни за 30 днів
            ax.axhline(
                y=avg_y,
                color=avg_line_color,
                linestyle=(0, (4, 4)),
                linewidth=1.1,
                alpha=0.65,
                zorder=3,
            )
            ax.text(
                plot_times[0],
                avg_y + spread * 0.03,
                f"  {t('chart_avg', lang)}: {_fmt_price(avg_y, currency)}",
                color=avg_line_color,
                fontsize=8.5,
                fontweight="bold",
                va="bottom",
                ha="left",
                zorder=5,
            )

            # 4. Лінія бажаної ціни (якщо встановлена)
            if target_price is not None and target_price > 0:
                ax.axhline(
                    y=target_price,
                    color=target_line_color,
                    linestyle="--",
                    linewidth=1.5,
                    alpha=0.9,
                    zorder=4,
                )
                ax.text(
                    plot_times[-1],
                    target_price + spread * 0.03,
                    f"{t('chart_target', lang)}: {_fmt_price(target_price, currency)}  ",
                    color=target_line_color,
                    fontsize=8.5,
                    fontweight="bold",
                    va="bottom",
                    ha="right",
                    zorder=5,
                )

            # 5. Капсульні бейджі МІН, МАКС та ЗАРАЗ
            min_idx = plot_prices.index(min_y)
            max_idx = plot_prices.index(max_y)
            min_ha = "left" if min_idx <= 1 else ("right" if min_idx >= len(plot_times) - 2 else "center")
            max_ha = "left" if max_idx <= 1 else ("right" if max_idx >= len(plot_times) - 2 else "center")

            # Мінімум
            ax.scatter(
                [plot_times[min_idx]],
                [min_y],
                color=min_badge_color,
                s=80,
                edgecolors=text_primary,
                linewidths=1.5,
                zorder=6,
            )
            ax.annotate(
                f"▼ {t('chart_min_badge', lang)} {_fmt_price(min_y, currency)}",
                xy=(plot_times[min_idx], min_y),
                xytext=(0, -22),
                textcoords="offset points",
                ha=min_ha,
                fontsize=8.5,
                fontweight="bold",
                color="#ECFDF5",
                bbox=dict(
                    boxstyle="round,pad=0.35,rounding_size=0.5",
                    facecolor="#065F46",
                    edgecolor=min_badge_color,
                    lw=1.2,
                ),
                zorder=7,
            )

            # Максимум (якщо відрізняється від мінімуму)
            if max_idx != min_idx:
                ax.scatter(
                    [plot_times[max_idx]],
                    [max_y],
                    color=max_badge_color,
                    s=80,
                    edgecolors=text_primary,
                    linewidths=1.5,
                    zorder=6,
                )
                ax.annotate(
                    f"▲ {t('chart_max_badge', lang)} {_fmt_price(max_y, currency)}",
                    xy=(plot_times[max_idx], max_y),
                    xytext=(0, 14),
                    textcoords="offset points",
                    ha=max_ha,
                    fontsize=8.5,
                    fontweight="bold",
                    color="#FFF1F2",
                    bbox=dict(
                        boxstyle="round,pad=0.35,rounding_size=0.5",
                        facecolor="#881337",
                        edgecolor=max_badge_color,
                        lw=1.2,
                    ),
                    zorder=7,
                )

            # Точка поточної ціни справа (зелена або червона залежно від динаміки)
            ax.scatter(
                [plot_times[-1]],
                [cur_y],
                color=current_dot_color,
                s=115,
                edgecolors="#FFFFFF",
                linewidths=2.2,
                zorder=7,
            )

            # 6. Оформлення осей у стилі фінансових терміналів (права шкала Y, без зайвих рамок)
            ax.yaxis.tick_right()
            ax.yaxis.set_label_position("right")
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: _fmt_price(x, currency)))

            ax.spines["top"].set_visible(False)
            ax.spines["left"].set_visible(False)
            ax.spines["right"].set_color(grid_color)
            ax.spines["bottom"].set_color(grid_color)

            ax.yaxis.grid(True, linestyle="-", linewidth=0.7, color=grid_color, alpha=0.85, zorder=1)
            ax.xaxis.grid(True, linestyle=":", linewidth=0.5, color=grid_color, alpha=0.35, zorder=1)

            ax.tick_params(axis="x", colors=text_muted, labelsize=8.5, rotation=0, pad=6)
            ax.tick_params(axis="y", colors=text_primary, labelsize=9.0, length=0, pad=8)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))

            # 7. Стильна верхня шапка (Header HUD)
            clean_market = format_marketplace_name(marketplace)
            short_title = title if len(title) <= 36 else f"{title[:33]}..."
            ax.set_title(
                f"{clean_market.upper()}  •  {short_title}",
                loc="left",
                color=text_primary,
                fontsize=10.5,
                fontweight="bold",
                pad=22,
            )
            ax.text(
                1.0,
                1.055,
                f"{t('chart_now', lang)}: {_fmt_price(cur_y, currency)}   |   {t('chart_min_30d', lang)}: {_fmt_price(min_y, currency)}",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                color=accent_color,
                fontsize=9.5,
                fontweight="bold",
            )

            fig.tight_layout(pad=1.4)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none")
            buf.seek(0)
            return buf
        finally:
            fig.clf()
            plt.close(fig)


async def generate_price_chart(
    session: AsyncSession,
    product_id: int,
    target_price: Decimal | None = None,
    lang: str = "uk",
    *,
    product: Product | None = None,
) -> io.BytesIO | None:
    """
    Асинхронно дістає `price_history` для `product_id` (або використовує вже завантажений `product`)
    та генерує PNG-графік у `io.BytesIO` в окремому пулі потоків (`asyncio.to_thread`).
    Повертає `None`, якщо товар не знайдено.
    """
    if product is None:
        product = await crud.get_product_with_history(session, product_id)
    if product is None:
        return None

    history: Sequence[PriceHistory] = product.price_history
    if not history:
        timestamps = [product.last_checked_at]
        prices = [float(product.current_price)]
    else:
        timestamps = [entry.timestamp for entry in history]
        prices = [float(entry.price) for entry in history]

    target_float = float(target_price) if target_price is not None else None

    return await asyncio.to_thread(
        _render_price_chart_sync,
        title=product.title,
        marketplace=product.marketplace,
        currency=product.currency,
        timestamps=timestamps,
        prices=prices,
        target_price=target_float,
        lang=lang,
    )
