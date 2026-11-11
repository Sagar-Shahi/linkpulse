from rest_framework.routers import SimpleRouter

from .api import LinkViewSet

router = SimpleRouter()
router.register('links', LinkViewSet, basename='api-link')

urlpatterns = router.urls
