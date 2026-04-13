from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Q, When
from django.utils import timezone

from .models import CrewMember, Maintenance, MaintenanceLog, Notification, Ship, ShipCrewRequirement, Voyage


SEVERITY_INFO = 'info'
SEVERITY_WARNING = 'warning'
SEVERITY_BLOCKING = 'blocking'
SEVERITY_LEVELS = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_BLOCKING)
SEVERITY_RANK = {
    SEVERITY_INFO: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_BLOCKING: 2,
}

MINIMUM_READY_CREW_ROLES = {
    'captain': 'Капитан',
    'first_mate': 'Старший помощник',
    'engineer': 'Механик',
}

RANK_LABELS = dict(CrewMember.RANK_CHOICES)

STATUS_BADGE_CLASSES = {
    'at_sea': 'bg-success',
    'in_port': 'bg-warning text-dark',
    'under_repair': 'bg-danger',
    'out_of_service': 'bg-secondary',
}

AUTO_LOCATION_MARKERS = {
    'В море (рейс)',
    'В порту (ожидание)',
    'На ремонте',
    'Судоремонтный завод',
    'Судоремонтный завод №2',
    'Судоремонтный завод №3',
    'В доке',
}

MANUAL_STATUS_OVERRIDES = {'out_of_service'}

MAINTENANCE_TOO_LONG_DAYS = 30
VOYAGE_TOO_LONG_DAYS = 60
CREW_CONTRACT_WARNING_DAYS = 7
VOYAGE_HEALTH_OVERDUE = 'voyage_overdue'
VOYAGE_HEALTH_TOO_LONG = 'voyage_too_long'
MAINTENANCE_HEALTH_OVERDUE_START = 'maintenance_overdue_start'
MAINTENANCE_HEALTH_TOO_LONG = 'maintenance_too_long'
MAINTENANCE_LOG_ALERT_CODE = 'maintenance_log_keyword_alert'
ENGINE_HOURS_THRESHOLD_CODE = 'maintenance_log_engine_hours_threshold'
CAPTAIN_MESSAGE_NOTIFICATION_CODE_PREFIX = 'captain_message_log_'
PLANNED_MAINTENANCE_INTERVAL_YEARS = getattr(settings, 'FLEET_PLANNED_MAINTENANCE_INTERVAL_YEARS', 5)
MAINTENANCE_LOG_REPAIR_KEYWORDS = tuple(
    keyword.lower()
    for keyword in getattr(
        settings,
        'FLEET_MAINTENANCE_LOG_REPAIR_KEYWORDS',
        ('поломка', 'неисправность', 'авария', 'отказ', 'ремонт'),
    )
)
ENGINE_HOURS_ALERT_THRESHOLD = getattr(settings, 'FLEET_ENGINE_HOURS_ALERT_THRESHOLD', 10000)
DEFAULT_REPAIR_WORKS = (
    ('engine', 'Двигатель', 1200000),
    ('hull', 'Корпус', 900000),
    ('propulsion', 'Винто-рулевая группа', 750000),
    ('navigation', 'Навигационное оборудование', 450000),
    ('electrical', 'Электрооборудование', 380000),
    ('refrigeration', 'Холодильное оборудование', 520000),
    ('deck', 'Палубные механизмы', 410000),
    ('fishing', 'Промысловое оборудование', 680000),
)
DEFAULT_SHIP_CREW_REQUIREMENTS = {
    'fishing': (
        ('captain', 1),
        ('first_mate', 1),
        ('engineer', 1),
        ('seaman', 2),
        ('cook', 1),
    ),
    'transport': (
        ('captain', 1),
        ('first_mate', 1),
        ('engineer', 1),
        ('seaman', 2),
        ('cook', 1),
    ),
    'passenger': (
        ('captain', 1),
        ('first_mate', 1),
        ('engineer', 1),
        ('seaman', 3),
        ('cook', 1),
    ),
}


@dataclass
class BusinessCheckIssue:
    severity: str
    message: str
    code: str = ''
    details: object = None
    context: dict = field(default_factory=dict)


@dataclass
class BusinessCheckResult:
    issues: list[BusinessCheckIssue] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def add_issue(self, severity, message, code='', details=None, context=None):
        self.issues.append(
            BusinessCheckIssue(
                severity=severity,
                message=message,
                code=code,
                details=details,
                context=context or {},
            )
        )

    def add_blocking(self, message, code='', details=None, context=None):
        self.add_issue(SEVERITY_BLOCKING, message, code=code, details=details, context=context)

    def add_warning(self, message, code='', details=None, context=None):
        self.add_issue(SEVERITY_WARNING, message, code=code, details=details, context=context)

    def add_info(self, message, code='', details=None, context=None):
        self.add_issue(SEVERITY_INFO, message, code=code, details=details, context=context)

    def extend(self, other):
        self.issues.extend(other.issues)
        if other.context:
            self.context.update(other.context)

    @property
    def is_ok(self):
        """Canonical business-check status: True when there are no blocking issues."""
        return not any(issue.severity == SEVERITY_BLOCKING for issue in self.issues)

    @property
    def is_ready(self):
        """Compatibility alias for readiness-oriented callers."""
        return self.is_ok

    @property
    def is_valid(self):
        """Compatibility alias for validation-oriented callers."""
        return self.is_ok

    @property
    def blocking_reasons(self):
        return [issue.message for issue in self.issues if issue.severity == SEVERITY_BLOCKING]

    @property
    def warnings(self):
        return [issue.message for issue in self.issues if issue.severity == SEVERITY_WARNING]

    @property
    def info_messages(self):
        return [issue.message for issue in self.issues if issue.severity == SEVERITY_INFO]

    @property
    def severity_summary(self):
        return {
            severity: sum(1 for issue in self.issues if issue.severity == severity)
            for severity in SEVERITY_LEVELS
        }


@dataclass
class ShipOperationalState:
    actual_status: str
    status_display: str
    badge_class: str
    location_display: str
    active_voyage: object = None
    active_maintenance: object = None


@dataclass
class OperationalHealthState:
    category: str
    status: str
    severity: str | None
    code: str | None
    title: str
    reason: str
    details: object = None


@dataclass(frozen=True)
class CanonicalProblemRule:
    category: str
    status: str
    code: str
    severity: str
    title: str


@dataclass
class SystemProblemState:
    category: str
    severity: str
    code: str
    title: str
    object: object
    reason: str
    details: object = None
    context: dict = field(default_factory=dict)


@dataclass
class SystemProblemsResult:
    issues: list[SystemProblemState] = field(default_factory=list)
    enriched_ships: dict[int, object] = field(default_factory=dict)

    def add(self, category, severity, code, title, object, reason, details=None, context=None):
        issue_key = _problem_object_key(category, object, code=code)
        existing_issue = next(
            (
                issue for issue in self.issues
                if _problem_object_key(issue.category, issue.object, code=issue.code) == issue_key
            ),
            None,
        )
        if existing_issue:
            if SEVERITY_RANK[severity] > SEVERITY_RANK[existing_issue.severity]:
                existing_issue.severity = severity
                existing_issue.code = code
                existing_issue.title = title
                existing_issue.reason = reason
            existing_issue.details = _merge_problem_details(existing_issue.details, details)
            if context:
                existing_issue.context.update(context)
            return

        self.issues.append(
            SystemProblemState(
                category=category,
                severity=severity,
                code=code,
                title=title,
                object=object,
                reason=reason,
                details=details,
                context=context or {},
            )
        )

    @property
    def grouped(self):
        grouped = {
            'ships': [],
            'voyages': [],
            'maintenances': [],
            'crew': [],
        }
        for issue in self._entity_issues():
            grouped.setdefault(issue.category, []).append(issue)
        return grouped

    @property
    def severity_summary(self):
        return {
            severity: sum(1 for issue in self._entity_issues() if issue.severity == severity)
            for severity in SEVERITY_LEVELS
        }

    @property
    def total_issues(self):
        return len(self._entity_issues())

    def _entity_issues(self):
        aggregated = {}
        for issue in self.issues:
            entity_key = _problem_object_key(issue.category, issue.object)
            if entity_key not in aggregated:
                aggregated[entity_key] = SystemProblemState(
                    category=issue.category,
                    severity=issue.severity,
                    code=issue.code,
                    title=issue.title,
                    object=issue.object,
                    reason=issue.reason,
                    details=issue.details,
                    context=dict(issue.context or {}),
                )
                continue

            existing = aggregated[entity_key]
            if SEVERITY_RANK[issue.severity] > SEVERITY_RANK[existing.severity]:
                existing.severity = issue.severity
                existing.code = issue.code
                existing.title = issue.title
                existing.reason = issue.reason
            existing.details = _merge_problem_details(existing.details, issue.details)
            if issue.context:
                existing.context.update(issue.context)

        return list(aggregated.values())


def _normalize_datetime(value):
    if value is None:
        return timezone.now()
    if hasattr(value, 'hour'):
        if timezone.is_naive(value):
            return timezone.make_aware(value)
        return value
    return timezone.make_aware(timezone.datetime.combine(value, timezone.datetime.min.time()))


def _clean_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    if not text or text.lower() == 'none':
        return ''
    return text


def _prefetched_related_list(instance, relation_name):
    prefetched_cache = getattr(instance, '_prefetched_objects_cache', {})
    if relation_name in prefetched_cache:
        return list(prefetched_cache[relation_name])
    return None


def _problem_object_key(category, object, code=None):
    key = (category, object.__class__, getattr(object, 'pk', id(object)))
    if code is not None:
        return key + (code,)
    return key


def _merge_problem_details(existing_details, new_details):
    if not existing_details:
        return new_details
    if not new_details:
        return existing_details

    if isinstance(existing_details, list) or isinstance(new_details, list):
        merged = []
        for details in (existing_details, new_details):
            if isinstance(details, list):
                for item in details:
                    if item and item not in merged:
                        merged.append(item)
            elif details and details not in merged:
                merged.append(details)
        return merged

    if existing_details == new_details:
        return existing_details
    return f'{existing_details}; {new_details}'


def _notification_entity_type(category):
    return {
        'ships': 'ship',
        'voyages': 'voyage',
        'maintenances': 'maintenance',
        'crew': 'crew',
    }[category]


VOYAGE_PROBLEM_RULES = (
    CanonicalProblemRule(
        category='voyages',
        status='overdue',
        code=VOYAGE_HEALTH_OVERDUE,
        severity=SEVERITY_BLOCKING,
        title='Рейс просрочен',
    ),
    CanonicalProblemRule(
        category='voyages',
        status='too_long',
        code=VOYAGE_HEALTH_TOO_LONG,
        severity=SEVERITY_WARNING,
        title='Рейс длится слишком долго',
    ),
)

MAINTENANCE_PROBLEM_RULES = (
    CanonicalProblemRule(
        category='maintenances',
        status='overdue_start',
        code=MAINTENANCE_HEALTH_OVERDUE_START,
        severity=SEVERITY_BLOCKING,
        title='Просрочен старт ремонта',
    ),
    CanonicalProblemRule(
        category='maintenances',
        status='too_long',
        code=MAINTENANCE_HEALTH_TOO_LONG,
        severity=SEVERITY_WARNING,
        title='Ремонт затянулся',
    ),
)


def _build_health_state(rule, reason, details=None):
    return OperationalHealthState(
        category=rule.category,
        status=rule.status,
        severity=rule.severity,
        code=rule.code,
        title=rule.title,
        reason=reason,
        details=details,
    )


def _voyage_is_overdue(voyage, now):
    return bool(voyage.end_date and voyage.end_date < now)


def _voyage_is_too_long(voyage, now):
    return voyage.start_date <= now - timezone.timedelta(days=VOYAGE_TOO_LONG_DAYS)


def _maintenance_is_overdue_start(maintenance, today):
    return maintenance.status == 'planned' and maintenance.start_date < today


def _maintenance_is_too_long(maintenance, today):
    return (
        maintenance.status == 'in_progress'
        and maintenance.start_date <= today - timezone.timedelta(days=MAINTENANCE_TOO_LONG_DAYS)
    )


def _voyage_problem_rule(voyage, now):
    if voyage.is_completed:
        return None
    if _voyage_is_overdue(voyage, now):
        return VOYAGE_PROBLEM_RULES[0]
    if _voyage_is_too_long(voyage, now):
        return VOYAGE_PROBLEM_RULES[1]
    return None


def _maintenance_problem_rule(maintenance, today):
    if _maintenance_is_overdue_start(maintenance, today):
        return MAINTENANCE_PROBLEM_RULES[0]
    if _maintenance_is_too_long(maintenance, today):
        return MAINTENANCE_PROBLEM_RULES[1]
    return None


def _voyage_problem_q(rule_code, now):
    if rule_code == VOYAGE_HEALTH_OVERDUE:
        return Q(is_completed=False, end_date__lt=now)
    if rule_code == VOYAGE_HEALTH_TOO_LONG:
        return Q(is_completed=False, start_date__lte=now - timezone.timedelta(days=VOYAGE_TOO_LONG_DAYS))
    raise ValueError(f'Unsupported voyage rule code: {rule_code}')


def _maintenance_problem_q(rule_code, today):
    if rule_code == MAINTENANCE_HEALTH_OVERDUE_START:
        return Q(status='planned', start_date__lt=today)
    if rule_code == MAINTENANCE_HEALTH_TOO_LONG:
        return Q(status='in_progress', start_date__lte=today - timezone.timedelta(days=MAINTENANCE_TOO_LONG_DAYS))
    raise ValueError(f'Unsupported maintenance rule code: {rule_code}')


def group_problem_issues_by_code(issues, category):
    grouped = {}
    for issue in issues:
        if issue.category != category:
            continue
        grouped.setdefault(issue.code, []).append(issue)
    return grouped


def evaluate_voyage_health(voyage, now=None):
    now = _normalize_datetime(now)
    if voyage.is_completed:
        return OperationalHealthState(
            category='voyages',
            status='normal',
            severity=None,
            code=None,
            title='Рейс в норме',
            reason='Рейс завершён.',
        )
    rule = _voyage_problem_rule(voyage, now)
    if rule and rule.code == VOYAGE_HEALTH_OVERDUE:
        return _build_health_state(
            rule,
            reason='Рейс просрочен (дата возвращения прошла)',
            details=f'Плановая дата возвращения: {voyage.end_date}',
        )
    if rule and rule.code == VOYAGE_HEALTH_TOO_LONG:
        return _build_health_state(
            rule,
            reason=f'Рейс идёт слишком долго (более {VOYAGE_TOO_LONG_DAYS} дней)',
            details=f'Начало: {voyage.start_date}',
        )
    return OperationalHealthState(
        category='voyages',
        status='normal',
        severity=None,
        code=None,
        title='Рейс в норме',
        reason='Рейс в нормальном состоянии.',
    )


def evaluate_maintenance_health(maintenance, today=None):
    today = today or timezone.now().date()
    rule = _maintenance_problem_rule(maintenance, today)
    if rule and rule.code == MAINTENANCE_HEALTH_OVERDUE_START:
        return _build_health_state(
            rule,
            reason='Запланированный ремонт с просроченной датой начала',
            details=f'Плановая дата начала: {maintenance.start_date}',
        )
    if rule and rule.code == MAINTENANCE_HEALTH_TOO_LONG:
        return _build_health_state(
            rule,
            reason=f'Ремонт в работе слишком долго (более {MAINTENANCE_TOO_LONG_DAYS} дней)',
            details=f'Начало: {maintenance.start_date}',
        )
    return OperationalHealthState(
        category='maintenances',
        status='normal',
        severity=None,
        code=None,
        title='Ремонт в норме',
        reason='Ремонт в нормальном состоянии.',
    )


def filter_voyages_by_health(queryset, health_filter='current', now=None):
    now = _normalize_datetime(now)
    if health_filter == 'overdue':
        return queryset.filter(_voyage_problem_q(VOYAGE_HEALTH_OVERDUE, now))
    if health_filter == 'too_long':
        return queryset.filter(_voyage_problem_q(VOYAGE_HEALTH_TOO_LONG, now)).exclude(
            _voyage_problem_q(VOYAGE_HEALTH_OVERDUE, now)
        )
    if health_filter == 'normal':
        return queryset.filter(
            is_completed=False,
            start_date__lte=now,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gt=now)
        ).exclude(
            _voyage_problem_q(VOYAGE_HEALTH_TOO_LONG, now)
        )
    return queryset.filter(
        is_completed=False,
        start_date__lte=now,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=now)
    )


def annotate_voyage_health_priority(queryset, now=None):
    now = _normalize_datetime(now)
    return queryset.annotate(
        criticality=Case(
            When(_voyage_problem_q(VOYAGE_HEALTH_OVERDUE, now), then=0),
            When(_voyage_problem_q(VOYAGE_HEALTH_TOO_LONG, now), then=1),
            default=2,
            output_field=IntegerField(),
        )
    )


def filter_maintenances_by_health(queryset, health_filter='all', today=None):
    today = today or timezone.now().date()
    if health_filter == 'overdue':
        return queryset.filter(_maintenance_problem_q(MAINTENANCE_HEALTH_OVERDUE_START, today))
    if health_filter == 'too_long':
        return queryset.filter(_maintenance_problem_q(MAINTENANCE_HEALTH_TOO_LONG, today))
    if health_filter == 'normal':
        return queryset.exclude(_maintenance_problem_q(MAINTENANCE_HEALTH_OVERDUE_START, today)).exclude(
            _maintenance_problem_q(MAINTENANCE_HEALTH_TOO_LONG, today)
        )
    return queryset


def annotate_maintenance_health_priority(queryset, today=None):
    today = today or timezone.now().date()
    return queryset.annotate(
        criticality=Case(
            When(_maintenance_problem_q(MAINTENANCE_HEALTH_OVERDUE_START, today), then=0),
            When(_maintenance_problem_q(MAINTENANCE_HEALTH_TOO_LONG, today), then=1),
            default=2,
            output_field=IntegerField(),
        )
    )


def _crew_requirements_snapshot(ship, crew_members, readiness_date):
    ensure_default_ship_crew_requirements(ship.type)
    requirements = list(ShipCrewRequirement.objects.filter(ship_type=ship.type).order_by('role'))
    actual_counts = {}
    eligible_counts = {}
    members_without_contract = {}
    expired_members = {}

    for member in crew_members:
        role = member.rank
        actual_counts[role] = actual_counts.get(role, 0) + 1
        if member.contract_end_date is None or member.contract_end_date >= readiness_date:
            eligible_counts[role] = eligible_counts.get(role, 0) + 1
        if member.contract_end_date is None:
            members_without_contract.setdefault(role, []).append(member)
        elif member.contract_end_date < readiness_date:
            expired_members.setdefault(role, []).append(member)

    requirement_rows = []
    missing_roles = []
    for requirement in requirements:
        role_label = RANK_LABELS.get(requirement.role, requirement.role)
        actual_count = actual_counts.get(requirement.role, 0)
        eligible_count = eligible_counts.get(requirement.role, 0)
        missing_count = max(requirement.required_count - eligible_count, 0)
        row = {
            'role': requirement.role,
            'role_display': role_label,
            'required_count': requirement.required_count,
            'actual_count': actual_count,
            'eligible_count': eligible_count,
            'missing_count': missing_count,
            'is_satisfied': missing_count == 0,
        }
        requirement_rows.append(row)
        if missing_count:
            missing_roles.append(row)

    return {
        'ship_type': ship.type,
        'ship_type_display': ship.get_type_display(),
        'requirements_configured': bool(requirements),
        'requirements': requirement_rows,
        'actual_counts': actual_counts,
        'eligible_counts': eligible_counts,
        'expired_members_by_role': {
            role: [member.user.get_full_name() or member.user.username for member in members]
            for role, members in expired_members.items()
        },
        'members_without_contract_by_role': {
            role: [member.user.get_full_name() or member.user.username for member in members]
            for role, members in members_without_contract.items()
        },
        'missing_roles': missing_roles,
    }


def _notification_message(problem_state):
    if isinstance(problem_state.details, list):
        details = '; '.join(str(item) for item in problem_state.details if item)
    else:
        details = str(problem_state.details) if problem_state.details else ''
    return f'{problem_state.reason}. {details}'.strip().rstrip('.')


def _notification_context(problem_state):
    context = _json_safe_value(problem_state.context or {})
    context.update(
        {
            'category': problem_state.category,
            'code': problem_state.code,
            'severity': problem_state.severity,
            'reason': problem_state.reason,
        }
    )
    return context


def _json_safe_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except TypeError:
            pass
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    if hasattr(value, 'pk'):
        return {
            'model': value.__class__.__name__,
            'id': value.pk,
        }
    if hasattr(value, 'issues') and hasattr(value, 'severity_summary'):
        return {
            'severity_summary': _json_safe_value(value.severity_summary),
            'blocking_reasons': _json_safe_value(getattr(value, 'blocking_reasons', [])),
            'warnings': _json_safe_value(getattr(value, 'warnings', [])),
        }
    return str(value)


def _get_status_label(ship, status_code):
    return dict(ship.STATUS_CHOICES).get(status_code, status_code)


def _manual_location(ship):
    current_location = _clean_text(ship.current_location)
    if current_location and current_location not in AUTO_LOCATION_MARKERS:
        return current_location
    return ''


def get_active_voyage(ship, at_datetime=None, exclude_voyage_id=None):
    at_datetime = _normalize_datetime(at_datetime)
    prefetched_voyages = _prefetched_related_list(ship, 'voyages')
    if prefetched_voyages is not None:
        matching_voyages = [
            voyage for voyage in prefetched_voyages
            if not voyage.is_completed
            and voyage.start_date <= at_datetime
            and (voyage.end_date is None or voyage.end_date > at_datetime)
            and (not exclude_voyage_id or voyage.id != exclude_voyage_id)
        ]
        matching_voyages.sort(key=lambda voyage: voyage.start_date, reverse=True)
        return matching_voyages[0] if matching_voyages else None

    queryset = ship.voyages.filter(
        is_completed=False,
        start_date__lte=at_datetime,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=at_datetime)
    )
    if exclude_voyage_id:
        queryset = queryset.exclude(id=exclude_voyage_id)
    return queryset.order_by('-start_date').first()


def get_active_maintenance(ship, at_datetime=None):
    at_datetime = _normalize_datetime(at_datetime)
    at_date = at_datetime.date()
    prefetched_maintenances = _prefetched_related_list(ship, 'maintenances')
    if prefetched_maintenances is not None:
        matching_maintenances = [
            maintenance for maintenance in prefetched_maintenances
            if maintenance.status == 'in_progress'
            and maintenance.start_date <= at_date
            and (maintenance.end_date is None or maintenance.end_date >= at_date)
        ]
        matching_maintenances.sort(key=lambda maintenance: maintenance.start_date, reverse=True)
        return matching_maintenances[0] if matching_maintenances else None

    return ship.maintenances.filter(
        status='in_progress',
        start_date__lte=at_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=at_date)
    ).order_by('-start_date').first()


def get_ship_operational_state(ship, at_datetime=None, exclude_voyage_id=None):
    at_datetime = _normalize_datetime(at_datetime)
    manual_location = _manual_location(ship)
    active_maintenance = get_active_maintenance(ship, at_datetime)
    active_voyage = get_active_voyage(ship, at_datetime, exclude_voyage_id=exclude_voyage_id)

    if ship.status in MANUAL_STATUS_OVERRIDES:
        status_code = 'out_of_service'
        location_display = manual_location or 'Выведено из эксплуатации'
    elif active_maintenance:
        status_code = 'under_repair'
        contractor_name = ''
        if active_maintenance.contractor:
            contractor_name = _clean_text(active_maintenance.contractor.name)
        elif active_maintenance.contractor_text:
            contractor_name = _clean_text(active_maintenance.contractor_text)
        location_display = contractor_name or manual_location or 'Зона ремонта'
    elif active_voyage:
        status_code = 'at_sea'
        location_display = _clean_text(active_voyage.get_fishing_area_display()) or manual_location or 'В море'
    else:
        status_code = 'in_port'
        location_display = manual_location or 'Порт не указан'

    return ShipOperationalState(
        actual_status=status_code,
        status_display=_get_status_label(ship, status_code),
        badge_class=STATUS_BADGE_CLASSES.get(status_code, 'bg-secondary'),
        location_display=location_display,
        active_voyage=active_voyage,
        active_maintenance=active_maintenance,
    )


def ship_readiness_service(ship, crew_members=None, at_datetime=None, exclude_voyage_id=None):
    at_datetime = _normalize_datetime(at_datetime)
    readiness_date = at_datetime.date()
    state = get_ship_operational_state(ship, at_datetime, exclude_voyage_id=exclude_voyage_id)
    result = BusinessCheckResult(
        context={
            'ship_id': ship.id,
            'ship_type': ship.type,
            'ship_type_display': ship.get_type_display(),
        }
    )

    if ship.status == 'out_of_service':
        result.add_blocking('Судно выведено из эксплуатации.', code='ship.out_of_service')

    if state.active_maintenance:
        result.add_blocking('Есть активный ремонт судна.', code='ship.active_maintenance')

    prefetched_voyages = _prefetched_related_list(ship, 'voyages')
    if prefetched_voyages is not None:
        unfinished_voyages = [
            voyage for voyage in prefetched_voyages
            if not voyage.is_completed and (not exclude_voyage_id or voyage.id != exclude_voyage_id)
        ]
        has_unfinished_voyage = bool(unfinished_voyages)
    else:
        unfinished_voyages = ship.voyages.filter(is_completed=False)
        if exclude_voyage_id:
            unfinished_voyages = unfinished_voyages.exclude(id=exclude_voyage_id)
        has_unfinished_voyage = unfinished_voyages.exists()
    if state.active_voyage:
        result.add_blocking('Есть незавершённый активный рейс.', code='ship.active_voyage')
    elif has_unfinished_voyage:
        result.add_blocking('Есть незавершённый рейс.', code='ship.unfinished_voyage')

    if crew_members is None:
        crew_members = ship.crew_members.select_related('user')

    crew_members = list(crew_members)
    has_no_crew = not crew_members
    if has_no_crew:
        result.add_blocking('Не назначен ни один член экипажа.', code='ship.no_crew')

    crew_requirement_context = _crew_requirements_snapshot(ship, crew_members, readiness_date)
    result.context['crew_requirements'] = crew_requirement_context

    if not crew_requirement_context['requirements_configured']:
        result.add_warning(
            f'Для типа судна "{ship.get_type_display()}" не настроены требования к минимальному составу экипажа.',
            code='ship.crew_requirements_not_configured',
            context={'ship_type': ship.type},
        )
        if has_no_crew:
            return result
    else:
        if has_no_crew:
            return result
        for requirement in crew_requirement_context['requirements']:
            if requirement['missing_count']:
                result.add_blocking(
                    f'Для роли "{requirement["role_display"]}" не хватает членов экипажа: '
                    f'требуется {requirement["required_count"]}, доступно {requirement["eligible_count"]}.',
                    code='ship.crew_requirement_shortfall',
                    context={
                        'role': requirement['role'],
                        'required_count': requirement['required_count'],
                        'eligible_count': requirement['eligible_count'],
                        'actual_count': requirement['actual_count'],
                        'missing_count': requirement['missing_count'],
                    },
                )

        for role, member_names in crew_requirement_context['members_without_contract_by_role'].items():
            if role not in {item['role'] for item in crew_requirement_context['requirements']}:
                continue
            role_label = RANK_LABELS.get(role, role)
            result.add_warning(
                f'У роли "{role_label}" не заполнена дата окончания контракта: {", ".join(member_names)}.',
                code='ship.required_role_contract_missing',
                context={'rank': role},
            )

        for role, member_names in crew_requirement_context['expired_members_by_role'].items():
            if role not in {item['role'] for item in crew_requirement_context['requirements']}:
                continue
            role_label = RANK_LABELS.get(role, role)
            result.add_warning(
                f'У части экипажа роли "{role_label}" истёк контракт: {", ".join(member_names)}.',
                code='ship.required_role_contract_expired',
                context={'rank': role},
            )

    soon_expiring_members = [
        member for member in crew_members
        if member.contract_end_date and readiness_date <= member.contract_end_date <= readiness_date + timezone.timedelta(days=30)
    ]
    if soon_expiring_members:
        names = ', '.join(member.user.get_full_name() or member.user.username for member in soon_expiring_members)
        result.add_warning(
            f'У части экипажа контракт истекает в ближайшие 30 дней: {names}.',
            code='ship.contracts_expiring_soon',
        )

    if ship.technical_condition:
        lowered_condition = ship.technical_condition.lower()
        if any(marker in lowered_condition for marker in ['ремонт', 'неисправ', 'дефект', 'авар']):
            result.add_warning(
                'Техническое состояние содержит замечания, проверьте карточку судна перед выходом.',
                code='ship.technical_condition_warning',
            )

    return result


def validate_crew_for_voyage_period(crew_members, voyage_start_date, planned_return_date=None, warning_window_days=CREW_CONTRACT_WARNING_DAYS):
    voyage_start_date = _normalize_datetime(voyage_start_date)
    planned_return_date = _normalize_datetime(planned_return_date) if planned_return_date else None
    crew_members = list(crew_members)
    result = BusinessCheckResult(
        context={
            'voyage_start_date': voyage_start_date,
            'planned_return_date': planned_return_date,
        }
    )

    for member in crew_members:
        member_name = member.user.get_full_name() or member.user.username
        role_name = member.get_rank_display()
        contract_end_date = member.contract_end_date
        issue_context = {
            'crew_member_id': member.id,
            'username': member.user.username,
            'rank': member.rank,
        }

        if contract_end_date is None:
            result.add_warning(
                f'У члена экипажа {member_name} ({role_name}) не указана дата окончания контракта.',
                code='crew.contract_missing',
                context=issue_context,
            )
            continue

        if contract_end_date < voyage_start_date.date():
            result.add_blocking(
                f'Контракт члена экипажа {member_name} ({role_name}) истёк до начала рейса: {contract_end_date:%d.%m.%Y}.',
                code='crew.contract_expired_before_start',
                context=issue_context,
            )
            continue

        if planned_return_date and voyage_start_date.date() <= contract_end_date < planned_return_date.date():
            result.add_blocking(
                f'Контракт члена экипажа {member_name} ({role_name}) заканчивается раньше планового завершения рейса: '
                f'{contract_end_date:%d.%m.%Y} < {planned_return_date:%d.%m.%Y}.',
                code='crew.contract_insufficient_for_voyage',
                context=issue_context,
            )
            continue

        if planned_return_date:
            warning_limit = planned_return_date.date() + timezone.timedelta(days=warning_window_days)
            if planned_return_date.date() <= contract_end_date <= warning_limit:
                result.add_warning(
                    f'Контракт члена экипажа {member_name} ({role_name}) заканчивается вскоре после рейса: {contract_end_date:%d.%m.%Y}.',
                    code='crew.contract_expires_soon_after_voyage',
                    context=issue_context,
                )

    return result


def enrich_ship_for_operations(ship, crew_members=None, at_datetime=None):
    state = get_ship_operational_state(ship, at_datetime=at_datetime)
    readiness = ship_readiness_service(ship, crew_members=crew_members, at_datetime=at_datetime)
    effective_last_repair_date = get_effective_last_repair_date(ship)
    planned_maintenance = get_planned_maintenance_status(ship)

    ship.actual_status_code = state.actual_status
    ship.actual_status_display = state.status_display
    ship.status_badge_class = state.badge_class
    ship.location_display = state.location_display
    ship.active_voyage_record = state.active_voyage
    ship.active_maintenance_record = state.active_maintenance
    ship.readiness = readiness
    ship.last_repair_date = effective_last_repair_date
    ship.planned_maintenance = planned_maintenance
    return ship


def get_system_problem_states():
    now = timezone.now()
    today = now.date()
    result = SystemProblemsResult()

    ships = Ship.objects.all().prefetch_related('crew_members__user', 'voyages', 'maintenances__contractor')
    for ship in ships:
        enriched_ship = enrich_ship_for_operations(ship)
        result.enriched_ships[ship.id] = enriched_ship
        if not enriched_ship.readiness.is_ok:
            result.add(
                category='ships',
                severity=SEVERITY_BLOCKING,
                code='ship_not_ready',
                title='Судно не готово к рейсу',
                object=ship,
                reason='Не готово к рейсу',
                details=enriched_ship.readiness.blocking_reasons,
                context={'readiness': enriched_ship.readiness, 'enriched': enriched_ship},
            )

    maintenances = Maintenance.objects.filter(status__in=['planned', 'in_progress']).select_related('ship', 'contractor')
    for maintenance in maintenances:
        maintenance_health = evaluate_maintenance_health(maintenance, today=today)
        if maintenance_health.code:
            result.add(
                category=maintenance_health.category,
                severity=maintenance_health.severity,
                code=maintenance_health.code,
                title=maintenance_health.title,
                object=maintenance,
                reason=maintenance_health.reason,
                details=maintenance_health.details,
            )

    voyages = Voyage.objects.filter(is_completed=False).select_related('ship')
    for voyage in voyages:
        voyage_health = evaluate_voyage_health(voyage, now=now)
        if voyage_health.code:
            result.add(
                category=voyage_health.category,
                severity=voyage_health.severity,
                code=voyage_health.code,
                title=voyage_health.title,
                object=voyage,
                reason=voyage_health.reason,
                details=voyage_health.details,
            )

    crew_expired = CrewMember.objects.filter(contract_end_date__lt=today).select_related('user', 'assigned_ship')
    for crew_member in crew_expired:
        result.add(
            category='crew',
            severity=SEVERITY_BLOCKING,
            code='crew_contract_expired',
            title='Истёк контракт экипажа',
            object=crew_member,
            reason='Контракт истёк',
            details=f'Дата окончания: {crew_member.contract_end_date}',
        )

    crew_expiring_soon = CrewMember.objects.filter(
        contract_end_date__gte=today,
        contract_end_date__lte=today + timezone.timedelta(days=CREW_CONTRACT_WARNING_DAYS),
    ).select_related('user', 'assigned_ship')
    for crew_member in crew_expiring_soon:
        days_left = (crew_member.contract_end_date - today).days
        result.add(
            category='crew',
            severity=SEVERITY_WARNING,
            code='crew_contract_expiring_soon',
            title='Контракт экипажа скоро истечёт',
            object=crew_member,
            reason=f'Контракт истекает через {days_left} дней',
            details=f'Дата окончания: {crew_member.contract_end_date}',
        )

    return result


def sync_notifications_from_problem_states():
    with transaction.atomic():
        now = timezone.now()
        problem_states = get_system_problem_states()
        active_keys = set()

        for problem_state in problem_states.issues:
            entity_type = _notification_entity_type(problem_state.category)
            entity_id = problem_state.object.pk
            active_keys.add((entity_type, entity_id, problem_state.code))

            Notification.objects.update_or_create(
                entity_type=entity_type,
                entity_id=entity_id,
                code=problem_state.code,
                defaults={
                    'severity': problem_state.severity,
                    'title': problem_state.title,
                    'message': _notification_message(problem_state),
                    'context': _notification_context(problem_state),
                    'is_resolved': False,
                    'resolved_at': None,
                },
            )

        unresolved_notifications = Notification.objects.filter(is_resolved=False)
        for notification in unresolved_notifications:
            notification_key = (notification.entity_type, notification.entity_id, notification.code)
            if notification_key not in active_keys:
                notification.is_resolved = True
                notification.resolved_at = now
                notification.save(update_fields=['is_resolved', 'resolved_at', 'updated_at'])

        return {
            'problem_states': problem_states,
            'active_keys': active_keys,
        }


def process_maintenance_log_event(log):
    with transaction.atomic():
        if _is_captain_log(log):
            _create_captain_message_notification(log)

        keyword_matches = _matched_maintenance_log_keywords(log.note)
        if keyword_matches:
            _create_or_update_maintenance_log_notification(
                log=log,
                code=MAINTENANCE_LOG_ALERT_CODE,
                severity=SEVERITY_BLOCKING,
                title='Требуется техническая проверка по сообщению капитана',
                message=(
                    f'Капитан сообщил о возможной неисправности по судну "{log.ship.name}". '
                    f'Ключевые слова: {", ".join(keyword_matches)}.'
                ),
                context={
                    'log_id': log.id,
                    'engine_hours': log.engine_hours,
                    'keywords': keyword_matches,
                },
            )
            _ensure_emergency_maintenance_for_log(log)

        if log.engine_hours >= ENGINE_HOURS_ALERT_THRESHOLD:
            _create_or_update_maintenance_log_notification(
                log=log,
                code=ENGINE_HOURS_THRESHOLD_CODE,
                severity=SEVERITY_WARNING,
                title='Превышен порог наработки судна',
                message=(
                    f'Наработка двигателя судна "{log.ship.name}" достигла {log.engine_hours} ч '
                    f'при пороге {ENGINE_HOURS_ALERT_THRESHOLD} ч.'
                ),
                context={
                    'log_id': log.id,
                    'engine_hours': log.engine_hours,
                    'threshold': ENGINE_HOURS_ALERT_THRESHOLD,
                },
            )


def _matched_maintenance_log_keywords(note):
    normalized_note = (note or '').lower()
    if not normalized_note:
        return []
    return [keyword for keyword in MAINTENANCE_LOG_REPAIR_KEYWORDS if keyword in normalized_note]


def _is_captain_log(log):
    author = getattr(log, 'author', None)
    if not author or not hasattr(author, 'role'):
        return False
    return author.role.role == 'captain'


def _create_captain_message_notification(log):
    message_preview = (log.note or '').strip()
    if len(message_preview) > 160:
        message_preview = message_preview[:157] + '...'

    Notification.objects.update_or_create(
        entity_type='ship',
        entity_id=log.ship_id,
        code=f'{CAPTAIN_MESSAGE_NOTIFICATION_CODE_PREFIX}{log.id}',
        defaults={
            'severity': SEVERITY_INFO,
            'title': f'Новое сообщение капитана по судну "{log.ship.name}"',
            'message': message_preview or 'Капитан оставил новое техническое сообщение.',
            'context': {
                'category': 'captain_message',
                'ship_id': log.ship_id,
                'log_id': log.id,
                'author_id': log.author_id,
            },
            'is_read': False,
            'is_resolved': False,
            'resolved_at': None,
        },
    )


def _create_or_update_maintenance_log_notification(log, code, severity, title, message, context):
    Notification.objects.update_or_create(
        entity_type='ship',
        entity_id=log.ship_id,
        code=code,
        defaults={
            'severity': severity,
            'title': title,
            'message': message,
            'context': {
                'category': 'maintenance_log',
                'ship_id': log.ship_id,
                **context,
            },
            'is_resolved': False,
            'resolved_at': None,
        },
    )


def _ensure_emergency_maintenance_for_log(log):
    active_emergency = Maintenance.objects.filter(
        ship=log.ship,
        maintenance_type='emergency',
        status__in=['planned', 'in_progress'],
    ).exists()
    if active_emergency:
        return None

    maintenance = Maintenance(
        ship=log.ship,
        maintenance_type='emergency',
        status='planned',
        start_date=timezone.now().date(),
        description=(
            'Автоматически создано по техническому сообщению капитана '
            f'от {log.recorded_at:%d.%m.%Y %H:%M}. '
            f'Наработка: {log.engine_hours} ч. '
            f'Заметка: {log.note or "без заметки"}'
        ),
    )
    try:
        maintenance.full_clean()
    except ValidationError:
        return None
    maintenance.save()
    return maintenance


def get_planned_maintenance_status(ship, reference_date=None):
    reference_date = reference_date or timezone.now().date()
    last_repair_date = get_effective_last_repair_date(ship)
    active_planned_maintenance = ensure_planned_maintenance_for_ship(ship, reference_date=reference_date)

    if last_repair_date is None:
        return {
            'interval_years': PLANNED_MAINTENANCE_INTERVAL_YEARS,
            'last_repair_date': None,
            'next_due_date': active_planned_maintenance.start_date if active_planned_maintenance else None,
            'is_due': active_planned_maintenance.start_date <= reference_date if active_planned_maintenance else False,
            'has_scheduled_maintenance': active_planned_maintenance is not None,
            'scheduled_maintenance': active_planned_maintenance,
            'reason': 'repair_date_missing',
        }

    next_due_date = active_planned_maintenance.start_date if active_planned_maintenance else _safe_add_years(
        last_repair_date,
        PLANNED_MAINTENANCE_INTERVAL_YEARS,
    )
    return {
        'interval_years': PLANNED_MAINTENANCE_INTERVAL_YEARS,
        'last_repair_date': last_repair_date,
        'next_due_date': next_due_date,
        'is_due': next_due_date <= reference_date,
        'has_scheduled_maintenance': active_planned_maintenance is not None,
        'scheduled_maintenance': active_planned_maintenance,
        'reason': 'interval_elapsed' if next_due_date <= reference_date else 'not_due',
    }


def get_effective_last_repair_date(ship):
    if ship.last_repair_date:
        return ship.last_repair_date

    latest_completed = ship.maintenances.filter(status='completed').order_by('-end_date', '-start_date').first()
    if latest_completed:
        return latest_completed.end_date or latest_completed.start_date

    return None


def ensure_planned_maintenance_for_ship(ship, reference_date=None):
    reference_date = reference_date or timezone.now().date()
    existing = ship.maintenances.filter(
        maintenance_type='planned',
        status__in=['planned', 'in_progress'],
    ).order_by('start_date').first()
    if existing:
        return existing

    anchor_date = get_effective_last_repair_date(ship) or reference_date
    next_due_date = _safe_add_years(anchor_date, PLANNED_MAINTENANCE_INTERVAL_YEARS)

    maintenance = Maintenance(
        ship=ship,
        maintenance_type='planned',
        description='Автоматически запланированный плановый ремонт.',
        start_date=next_due_date,
        status='planned',
        cost=0,
    )
    try:
        maintenance.full_clean()
    except ValidationError:
        return None
    maintenance.save()
    return maintenance


def ensure_default_repair_works():
    from .models import RepairWork

    for code, name, base_cost in DEFAULT_REPAIR_WORKS:
        RepairWork.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'base_cost': base_cost,
            },
        )


def ensure_default_ship_crew_requirements(ship_type=None):
    ship_types = [ship_type] if ship_type else list(DEFAULT_SHIP_CREW_REQUIREMENTS.keys())
    for current_ship_type in ship_types:
        for role, required_count in DEFAULT_SHIP_CREW_REQUIREMENTS.get(current_ship_type, ()):
            ShipCrewRequirement.objects.update_or_create(
                ship_type=current_ship_type,
                role=role,
                defaults={'required_count': required_count},
            )


def _safe_add_years(value, years):
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return date(value.year + years, 2, 28)
