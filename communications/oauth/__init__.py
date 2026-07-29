"""
OAuth flows, one module per identity provider.

Kept separate from `providers/` because the two answer different
questions: `oauth/` is how a company *grants* access once, `providers/` is
how we *use* it on every request afterwards. A future Microsoft
integration would add `microsoft_oauth.py` here and `microsoft_provider.py`
there, and they'd be about equally independent.
"""
