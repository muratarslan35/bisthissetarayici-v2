import pandas as pd
import numpy as np

def safe_get(df, col, default=None):
    if col in df.columns:
        return df[col]
    return default

def bool_any(series):
    if series is None:
        return False
    return bool(series.any())

# burada başka yardımcı fonksiyonlar ekleyebilirsin
