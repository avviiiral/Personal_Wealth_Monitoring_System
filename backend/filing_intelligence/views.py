from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view,permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Filing
from .serializers import FilingSerializer

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def filing_detail(request,filing_id):
    filing=get_object_or_404(Filing.objects.filter(portfolio_news_alerts__user=request.user).distinct(),id=filing_id)
    return Response(FilingSerializer(filing).data)
