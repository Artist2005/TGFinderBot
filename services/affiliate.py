from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from config import settings


def build_affiliate_url(clean_url: str, user_id: int | None = None) -> str:
    """
    Формує партнерське (CPA) посилання для кнопки «Відкрити в магазині» / «Купити».

    Підтримує 2 режими:
    1. Якщо задано `AFFILIATE_DEEPLINK_BASE` (наприклад, Admitad / SalesDoubler редірект-домен),
       обгортає цільовий URL у deeplink формату:
       `{DEEPLINK_BASE}?ulp={encoded_url}&subid={AFFILIATE_SUBID_KEY}&subid1={user_id}`
    2. Інакше додає партнерські GET-параметри (`aff_sub`, `utm_medium=cpa`) безпосередньо до URL.
    """
    subid = settings.affiliate_subid_key.strip()
    deeplink_base = (settings.affiliate_deeplink_base or "").strip()

    if deeplink_base:
        sep = "&" if "?" in deeplink_base else "?"
        params: dict[str, str] = {}
        if subid:
            params["subid"] = subid
        if user_id is not None:
            params["subid1"] = str(user_id)
        query_prefix = f"{urlencode(params)}&" if params else ""
        return f"{deeplink_base}{sep}{query_prefix}ulp={quote(clean_url, safe='')}"

    if not subid:
        return clean_url

    parsed = urlparse(clean_url)
    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params["aff_sub"] = subid
    if user_id is not None:
        query_params["aff_uid"] = str(user_id)

    new_query = urlencode(query_params, doseq=True)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )
