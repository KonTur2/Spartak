from .models import Notification


def fleet_notification_badges(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {
            'fleet_unread_notifications_count': 0,
        }

    user_role = getattr(getattr(request.user, 'role', None), 'role', None)
    if user_role not in {'director', 'fleet_manager', 'dispatcher', 'engineer', 'crew'}:
        return {
            'fleet_unread_notifications_count': 0,
        }

    return {
        'fleet_unread_notifications_count': Notification.objects.filter(is_read=False).count(),
    }
