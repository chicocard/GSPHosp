"""Tela "Atratividade por faixas de distância" — ver papahr.ui.atratividade.widget.

    python -m papahr.ui.atratividade     # demonstração com dados sintéticos (as duas abas)
"""


def __getattr__(nome):
    if nome == "AtratividadeFaixasWidget":
        from papahr.ui.atratividade.widget import AtratividadeFaixasWidget
        return AtratividadeFaixasWidget
    raise AttributeError(nome)
