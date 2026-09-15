import re
from datetime import date
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone

from .models import DiscoveryRun, InvestmentProduct, MutualFundProduct, ProductType, PerformanceSnapshot
