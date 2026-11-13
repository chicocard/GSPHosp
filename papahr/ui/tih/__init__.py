"""Aba "Taxas municipais de internação" (TIH) — ver papahr.ui.tih.widget."""


def __getattr__(nome):
    if nome == "TaxasMunicipaisWidget":
        from papahr.ui.tih.widget import TaxasMunicipaisWidget
        return TaxasMunicipaisWidget
    raise AttributeError(nome)
