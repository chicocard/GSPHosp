"""Module __init__ para fontes de dados externas."""
from papahr.datasources.counties import CountiesRepository, CountyData
from papahr.datasources.cnes import CNESRepository
from papahr.datasources.sigtap import SIGTAPRepository

__all__ = [
    "CountiesRepository",
    "CountyData",
    "CNESRepository",
    "SIGTAPRepository",
]
