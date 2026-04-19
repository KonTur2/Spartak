from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.core.exceptions import ValidationError

from fleet.models import CrewMember, Maintenance, MaintenanceLog, Notification, RepairWork, Ship, ShipCrewRequirement, Voyage
from fleet.services import (
    MINIMUM_READY_CREW_ROLES,
    BusinessCheckResult,
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    annotate_maintenance_health_priority,
    annotate_voyage_health_priority,
    ensure_default_ship_crew_requirements,
    evaluate_maintenance_health,
    evaluate_voyage_health,
    filter_maintenances_by_health,
    filter_voyages_by_health,
    get_system_problem_states,
    get_planned_maintenance_status,
    get_ship_operational_state,
    group_problem_issues_by_code,
    ship_readiness_service,
    sync_notifications_from_problem_states,
    validate_crew_for_voyage_period,
)
from fleet.views import _parse_local_date, _parse_local_datetime


class FleetModuleTests(TestCase):
    def setUp(self):
        self.dispatcher = User.objects.create_user(username='dispatcher_test', password='pass12345')
        self.dispatcher.role.role = 'dispatcher'
        self.dispatcher.role.save()

        self.ship = Ship.objects.create(
            name='Север',
            imo_number='IMO1000001',
            type='fishing',
        )
        self.other_ship = Ship.objects.create(
            name='Восток',
            imo_number='IMO1000002',
            type='fishing',
        )

        self.seaman_user = User.objects.create_user(
            username='crew_member_test',
            password='pass12345',
            first_name='Иван',
            last_name='Морской',
        )
        self.seaman_user.role.role = 'crew'
        self.seaman_user.role.save()
        self.seaman = CrewMember.objects.create(
            user=self.seaman_user,
            rank='seaman',
            assigned_ship=self.ship,
        )
        self._create_requirement('fishing', 'captain', 1)
        self._create_requirement('fishing', 'first_mate', 1)
        self._create_requirement('fishing', 'engineer', 1)

    def _create_requirement(self, ship_type, role, required_count):
        return ShipCrewRequirement.objects.create(
            ship_type=ship_type,
            role=role,
            required_count=required_count,
        )

    def _create_minimum_required_crew(self, ship, expired_captain=False):
        members = []
        for rank, username, first_name in [
            ('captain', 'captain_test', 'Петр'),
            ('first_mate', 'mate_test', 'Алексей'),
            ('engineer', 'engineer_test', 'Денис'),
        ]:
            user = User.objects.create_user(
                username=f'{username}_{ship.id}',
                password='pass12345',
                first_name=first_name,
                last_name='Экипаж',
            )
            user.role.role = 'crew'
            user.role.save()
            contract_end = timezone.now().date() + timezone.timedelta(days=30)
            if expired_captain and rank == 'captain':
                contract_end = timezone.now().date() - timezone.timedelta(days=1)
            members.append(
                CrewMember.objects.create(
                    user=user,
                    rank=rank,
                    assigned_ship=ship,
                    contract_end_date=contract_end,
                )
            )
        return members

    def _create_named_crew_member(self, ship, rank, username, contract_end_date):
        user = User.objects.create_user(
            username=username,
            password='pass12345',
            first_name='Тест',
            last_name=username,
        )
        user.role.role = 'crew'
        user.role.save()
        return CrewMember.objects.create(
            user=user,
            rank=rank,
            assigned_ship=ship,
            contract_end_date=contract_end_date,
        )

    def _create_user_with_role(self, username, role):
        user = User.objects.create_user(username=username, password='pass12345')
        user.role.role = role
        user.role.save()
        return user

    def test_ship_actual_status_uses_open_ended_voyage(self):
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=2),
            end_date=None,
            is_completed=False,
        )

        self.assertEqual(self.ship.get_actual_status(), 'at_sea')

    def test_parse_local_date_accepts_russian_and_iso_formats(self):
        self.assertEqual(_parse_local_date('13.04.2026').isoformat(), '2026-04-13')
        self.assertEqual(_parse_local_date('2026-04-13').isoformat(), '2026-04-13')

    def test_parse_local_datetime_accepts_russian_and_iso_formats(self):
        self.assertEqual(_parse_local_datetime('13.04.2026 08:45').strftime('%Y-%m-%d %H:%M'), '2026-04-13 08:45')
        self.assertEqual(_parse_local_datetime('2026-04-13T08:45').strftime('%Y-%m-%d %H:%M'), '2026-04-13 08:45')

    def test_dashboard_combined_ship_filters_return_expected_ships(self):
        self._create_minimum_required_crew(self.ship)
        self.client.login(username='dispatcher_test', password='pass12345')

        response = self.client.get(
            reverse('fleet:fleet_dashboard'),
            {'ship_filter': 'ready', 'crew_filter': 'complete', 'ship_sort': 'name'},
        )

        self.assertEqual(response.status_code, 200)
        ships = list(response.context['ships'])
        self.assertEqual([ship.id for ship in ships], [self.ship.id])
        self.assertEqual(response.context['ship_filter'], 'ready')
        self.assertEqual(response.context['crew_filter'], 'complete')
        self.assertEqual(response.context['ship_sort'], 'name')

    def test_dashboard_invalid_filters_fall_back_to_defaults(self):
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=1),
            end_date=timezone.now() + timezone.timedelta(days=1),
            is_completed=False,
        )
        self.client.login(username='dispatcher_test', password='pass12345')

        response = self.client.get(
            reverse('fleet:fleet_dashboard'),
            {
                'ship_filter': 'broken',
                'crew_filter': '',
                'ship_sort': 'unknown',
                'voyage_filter': 'bad',
                'voyage_sort': 'oops',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ship_filter'], 'all')
        self.assertEqual(response.context['crew_filter'], 'all')
        self.assertEqual(response.context['ship_sort'], 'name')
        self.assertEqual(response.context['voyage_filter'], 'current')
        self.assertEqual(response.context['voyage_sort'], 'date')
        self.assertEqual(response.context['current_voyages'].count(), 1)

    def test_dashboard_removed_filters_fall_back_to_supported_values(self):
        self.client.login(username='dispatcher_test', password='pass12345')

        response = self.client.get(
            reverse('fleet:fleet_dashboard'),
            {
                'crew_filter': 'not_configured',
                'voyage_filter': 'too_long',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['crew_filter'], 'all')
        self.assertEqual(response.context['voyage_filter'], 'current')

    def test_default_fishing_crew_requirements_are_created(self):
        ShipCrewRequirement.objects.all().delete()

        ensure_default_ship_crew_requirements('fishing')

        requirement_map = {
            item.role: item.required_count
            for item in ShipCrewRequirement.objects.filter(ship_type='fishing')
        }
        self.assertEqual(
            requirement_map,
            {
                'captain': 1,
                'first_mate': 1,
                'engineer': 1,
                'seaman': 2,
                'cook': 1,
            },
        )

    def test_default_transport_and_passenger_requirements_are_created(self):
        ShipCrewRequirement.objects.filter(ship_type__in=['transport', 'passenger']).delete()

        ensure_default_ship_crew_requirements('transport')
        ensure_default_ship_crew_requirements('passenger')

        transport_map = {
            item.role: item.required_count
            for item in ShipCrewRequirement.objects.filter(ship_type='transport')
        }
        passenger_map = {
            item.role: item.required_count
            for item in ShipCrewRequirement.objects.filter(ship_type='passenger')
        }

        self.assertEqual(
            transport_map,
            {
                'captain': 1,
                'first_mate': 1,
                'engineer': 1,
                'seaman': 2,
                'cook': 1,
            },
        )
        self.assertEqual(
            passenger_map,
            {
                'captain': 1,
                'first_mate': 1,
                'engineer': 1,
                'seaman': 3,
                'cook': 1,
            },
        )

    def test_dashboard_voyage_filter_reduces_queryset(self):
        overdue_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=3),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_completed=False,
        )
        Voyage.objects.create(
            ship=self.other_ship,
            start_date=timezone.now() - timezone.timedelta(days=1),
            end_date=timezone.now() + timezone.timedelta(days=2),
            is_completed=False,
        )
        self.client.login(username='dispatcher_test', password='pass12345')

        response = self.client.get(
            reverse('fleet:fleet_dashboard'),
            {'voyage_filter': 'overdue', 'voyage_sort': 'date'},
        )

        self.assertEqual(response.status_code, 200)
        voyages = list(response.context['current_voyages'])
        self.assertEqual([voyage.id for voyage in voyages], [overdue_voyage.id])

    def test_maintenance_list_removed_filters_fall_back_to_all(self):
        self.client.login(username='dispatcher_test', password='pass12345')

        response = self.client.get(
            reverse('fleet:maintenance_list'),
            {'maintenance_filter': 'too_long'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['maintenance_filter'], 'all')

    def test_crew_list_can_filter_reserve_members(self):
        manager = self._create_user_with_role('fleet_manager_reserve', 'fleet_manager')
        reserve_user = self._create_user_with_role('reserve_member', 'crew')
        reserve_member = CrewMember.objects.create(user=reserve_user, rank='engineer', assigned_ship=None)
        self.client.login(username='fleet_manager_reserve', password='pass12345')

        response = self.client.get(reverse('fleet:crew_list'), {'assignment': 'reserve'})

        self.assertEqual(response.status_code, 200)
        crew_ids = list(response.context['crew'].values_list('id', flat=True))
        self.assertIn(reserve_member.id, crew_ids)
        self.assertNotIn(self.seaman.id, crew_ids)

    def test_fleet_manager_can_assign_reserve_member_to_ship(self):
        manager = self._create_user_with_role('fleet_manager_assign', 'fleet_manager')
        reserve_user = self._create_user_with_role('reserve_assignable', 'crew')
        reserve_member = CrewMember.objects.create(user=reserve_user, rank='seaman', assigned_ship=None)
        self.client.login(username='fleet_manager_assign', password='pass12345')

        response = self.client.post(
            reverse('fleet:crew_edit', args=[reserve_member.id]),
            {
                'email': 'reserve@example.com',
                'first_name': 'Резерв',
                'last_name': 'Назначаемый',
                'patronymic': 'Игоревич',
                'rank': 'seaman',
                'contract_end_date': '2027-12-31',
                'assigned_ship': str(self.ship.id),
                'password': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        reserve_member.refresh_from_db()
        self.assertEqual(reserve_member.assigned_ship_id, self.ship.id)
        self.assertEqual(reserve_member.contract_end_date.isoformat(), '2027-12-31')
        self.assertEqual(reserve_member.notes, 'Отчество: Игоревич')

    def test_ship_crew_assign_rejects_incomplete_fishing_crew(self):
        manager = self._create_user_with_role('fleet_manager_ship_crew', 'fleet_manager')
        captain = self._create_user_with_role('reserve_captain_only', 'captain')
        captain_member = CrewMember.objects.create(user=captain, rank='captain', assigned_ship=None)
        self.client.login(username='fleet_manager_ship_crew', password='pass12345')

        response = self.client.post(
            reverse('fleet:ship_crew_assign', args=[self.ship.id]),
            {'crew_members': [str(captain_member.id)]},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('Нельзя сохранить состав экипажа' in str(item) for item in response.context['messages']))
        captain_member.refresh_from_db()
        self.assertIsNone(captain_member.assigned_ship)

    def test_ship_crew_assign_saves_complete_fishing_crew(self):
        manager = self._create_user_with_role('fleet_manager_ship_crew_ok', 'fleet_manager')
        self.client.login(username='fleet_manager_ship_crew_ok', password='pass12345')
        reserve_members = []
        for rank, username in [
            ('captain', 'reserve_captain_ok'),
            ('first_mate', 'reserve_mate_ok'),
            ('engineer', 'reserve_engineer_ok'),
            ('seaman', 'reserve_seaman_ok_1'),
            ('seaman', 'reserve_seaman_ok_2'),
            ('cook', 'reserve_cook_ok'),
        ]:
            role = 'captain' if rank == 'captain' else 'crew'
            reserve_user = self._create_user_with_role(username, role)
            reserve_members.append(
                CrewMember.objects.create(
                    user=reserve_user,
                    rank=rank,
                    assigned_ship=None,
                    contract_end_date=timezone.now().date() + timezone.timedelta(days=120),
                )
            )

        response = self.client.post(
            reverse('fleet:ship_crew_assign', args=[self.ship.id]),
            {'crew_members': [str(member.id) for member in reserve_members]},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CrewMember.objects.filter(assigned_ship=self.ship).count(), 6)

    def test_ship_supports_optional_engine_hours_and_last_repair_date(self):
        self.assertIsNone(self.ship.engine_hours)
        self.assertIsNone(self.ship.last_repair_date)

        self.ship.engine_hours = 1200
        self.ship.last_repair_date = timezone.now().date()
        self.ship.save(update_fields=['engine_hours', 'last_repair_date'])
        self.ship.refresh_from_db()

        self.assertEqual(self.ship.engine_hours, 1200)
        self.assertIsNotNone(self.ship.last_repair_date)

    def test_cannot_assign_second_captain_to_same_ship(self):
        first_user = self._create_user_with_role('captain_primary', 'captain')
        second_user = self._create_user_with_role('captain_secondary', 'captain')
        CrewMember.objects.create(user=first_user, rank='captain', assigned_ship=self.ship)

        duplicate = CrewMember(
            user=second_user,
            rank='captain',
            assigned_ship=self.ship,
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_planned_maintenance_is_due_without_last_repair_date(self):
        status = get_planned_maintenance_status(self.ship)

        self.assertFalse(status['is_due'])
        self.assertIsNone(status['last_repair_date'])
        self.assertEqual(
            status['next_due_date'],
            timezone.now().date().replace(year=timezone.now().date().year + 5),
        )
        self.assertTrue(status['has_scheduled_maintenance'])
        self.assertEqual(status['reason'], 'repair_date_missing')

    def test_planned_maintenance_is_not_due_after_recent_repair(self):
        self.ship.last_repair_date = timezone.now().date() - timezone.timedelta(days=365)
        self.ship.save(update_fields=['last_repair_date'])

        status = get_planned_maintenance_status(self.ship)

        self.assertFalse(status['is_due'])
        self.assertEqual(status['reason'], 'not_due')
        self.assertIsNotNone(status['next_due_date'])

    def test_maintenance_cost_is_sum_of_selected_repair_works(self):
        engine = RepairWork.objects.create(code='engine_test', name='Engine overhaul', base_cost=1000)
        hull = RepairWork.objects.create(code='hull_test', name='Hull inspection', base_cost=2500)
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Комплекс плановых работ',
            start_date=timezone.now().date(),
            status='planned',
        )

        maintenance.repair_works.set([engine, hull])
        maintenance.refresh_from_db()

        self.assertEqual(maintenance.cost, 3500)

    def test_maintenance_pages_support_legacy_records_without_repair_works(self):
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Старый ремонт без состава работ',
            start_date=timezone.now().date(),
            status='planned',
            cost=0,
        )
        self.client.login(username='dispatcher_test', password='pass12345')

        list_response = self.client.get(reverse('fleet:maintenance_list'))
        detail_response = self.client.get(reverse('fleet:maintenance_detail', args=[maintenance.id]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Старый ремонт без состава работ')

    def test_engineer_can_create_planned_maintenance_with_repair_works(self):
        engineer_user = self._create_user_with_role('maintenance_engineer', 'engineer')
        engine = RepairWork.objects.create(code='create_engine', name='Engine package', base_cost=1500)
        hull = RepairWork.objects.create(code='create_hull', name='Hull package', base_cost=500)
        self.client.login(username='maintenance_engineer', password='pass12345')

        response = self.client.post(
            reverse('fleet:maintenance_create'),
            {
                'ship': str(self.ship.id),
                'maintenance_type': 'planned',
                'description': 'Плановый ремонт через форму',
                'start_date': timezone.now().date().isoformat(),
                'end_date': '',
                'cost': '0',
                'status': 'planned',
                'contractor': '',
                'contractor_text': '',
                'repair_works': [str(engine.id), str(hull.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        maintenance = Maintenance.objects.get(description='Плановый ремонт через форму')
        self.assertEqual(maintenance.maintenance_type, 'planned')
        self.assertEqual(maintenance.cost, 2000)
        self.assertEqual(maintenance.repair_works.count(), 2)

        detail_response = self.client.get(reverse('fleet:maintenance_detail', args=[maintenance.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Плановый ремонт через форму')

    def test_engineer_sees_repair_edit_actions(self):
        engineer_user = self._create_user_with_role('maintenance_engineer_actions', 'engineer')
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Ремонт для проверки действий инженера',
            start_date=timezone.now().date(),
            status='planned',
        )
        self.client.login(username='maintenance_engineer_actions', password='pass12345')

        list_response = self.client.get(reverse('fleet:maintenance_list'))
        detail_response = self.client.get(reverse('fleet:maintenance_detail', args=[maintenance.id]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(list_response, reverse('fleet:maintenance_edit', args=[maintenance.id]))
        self.assertContains(detail_response, reverse('fleet:maintenance_edit', args=[maintenance.id]))
        self.assertContains(detail_response, reverse('fleet:maintenance_complete', args=[maintenance.id]))

    def test_crew_create_assigns_captain_role_for_captain_rank(self):
        manager = self._create_user_with_role('fleet_manager_create_captain', 'fleet_manager')
        self.client.login(username='fleet_manager_create_captain', password='pass12345')

        response = self.client.post(
            reverse('fleet:crew_create'),
            {
                'username': 'created_captain_user',
                'password': 'pass12345',
                'email': 'captain@example.com',
                'first_name': 'Иван',
                'last_name': 'Капитан',
                'patronymic': 'Тестовый',
                'rank': 'captain',
                'contract_end_date': '',
                'assigned_ship': str(self.ship.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        created_user = User.objects.get(username='created_captain_user')
        self.assertEqual(created_user.role.role, 'captain')
        self.assertEqual(created_user.crew_profile.rank, 'captain')

    def test_completing_planned_maintenance_updates_last_repair_date_via_view(self):
        engineer_user = self._create_user_with_role('maintenance_engineer_complete', 'engineer')
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Плановый ремонт для завершения',
            start_date=timezone.now().date() - timezone.timedelta(days=2),
            status='in_progress',
        )
        self.client.login(username='maintenance_engineer_complete', password='pass12345')

        response = self.client.post(reverse('fleet:maintenance_complete', args=[maintenance.id]))

        self.assertEqual(response.status_code, 302)
        maintenance.refresh_from_db()
        self.ship.refresh_from_db()
        self.assertEqual(maintenance.status, 'completed')
        self.assertEqual(self.ship.last_repair_date, maintenance.end_date)

    def test_maintenance_supports_multiple_repair_works(self):
        engine = RepairWork.objects.create(code='multi_engine', name='Engine diagnostics', base_cost=800)
        navigation = RepairWork.objects.create(code='multi_navigation', name='Navigation check', base_cost=1200)
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Проверка нескольких систем',
            start_date=timezone.now().date(),
            status='planned',
        )

        maintenance.repair_works.set([engine, navigation])

        self.assertEqual(maintenance.repair_works.count(), 2)
        self.assertEqual(
            list(maintenance.repair_works.order_by('code').values_list('code', flat=True)),
            ['multi_engine', 'multi_navigation'],
        )

    def test_completed_planned_maintenance_updates_last_repair_date(self):
        completed_date = timezone.now().date()
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Завершённый плановый ремонт',
            start_date=completed_date - timezone.timedelta(days=3),
            end_date=completed_date,
            status='completed',
        )

        self.ship.refresh_from_db()

        self.assertEqual(self.ship.last_repair_date, completed_date)
        next_planned = Maintenance.objects.filter(
            ship=self.ship,
            maintenance_type='planned',
            status='planned',
        ).exclude(start_date=completed_date - timezone.timedelta(days=3)).order_by('-start_date').first()
        self.assertIsNotNone(next_planned)
        self.assertEqual(next_planned.start_date, completed_date.replace(year=completed_date.year + 5))

    def test_ship_detail_uses_latest_completed_maintenance_as_last_repair_date(self):
        completed_date = timezone.now().date() - timezone.timedelta(days=10)
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Завершённый ремонт для карточки судна',
            start_date=completed_date - timezone.timedelta(days=2),
            end_date=completed_date,
            status='completed',
        )
        self.client.login(username='dispatcher_test', password='pass12345')

        response = self.client.get(reverse('fleet:ship_detail', args=[self.ship.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ship'].last_repair_date, completed_date)

    def test_ship_create_schedules_planned_maintenance_for_five_years(self):
        manager = self._create_user_with_role('fleet_manager_creator', 'fleet_manager')
        self.client.login(username='fleet_manager_creator', password='pass12345')
        today = timezone.now().date()

        response = self.client.post(
            reverse('fleet:ship_create'),
            {
                'name': 'Новый траулер',
                'imo': 'IMO5550001',
                'type': 'fishing',
                'current_location': 'Порт Владивосток',
                'technical_condition': 'Исправно',
                'engine_hours': '',
                'last_repair_date': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        ship = Ship.objects.get(imo_number='IMO5550001')
        planned = Maintenance.objects.get(
            ship=ship,
            maintenance_type='planned',
            status='planned',
        )
        self.assertEqual(planned.start_date, today.replace(year=today.year + 5))

    def test_emergency_maintenance_continues_to_work_with_repair_works(self):
        emergency_work = RepairWork.objects.create(
            code='emergency_drive',
            name='Emergency drive repair',
            base_cost=4000,
        )
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='emergency',
            description='Аварийный ремонт привода',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            status='in_progress',
        )

        maintenance.repair_works.set([emergency_work])
        maintenance.refresh_from_db()

        self.assertEqual(maintenance.cost, 4000)
        self.ship.refresh_from_db()
        self.assertEqual(self.ship.get_actual_status(), 'under_repair')

    def test_emergency_maintenance_without_repair_works_remains_accessible(self):
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='emergency',
            description='Аварийный ремонт без состава работ',
            start_date=timezone.now().date(),
            status='planned',
        )
        self.client.login(username='dispatcher_test', password='pass12345')

        list_response = self.client.get(reverse('fleet:maintenance_list'))
        detail_response = self.client.get(reverse('fleet:maintenance_detail', args=[maintenance.id]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(maintenance.repair_works.count(), 0)

    def test_maintenance_log_allows_first_entry(self):
        log = MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=120,
            note='Первичная запись капитана',
            author=self.dispatcher,
        )

        self.assertEqual(MaintenanceLog.objects.filter(ship=self.ship).count(), 1)
        self.assertEqual(log.engine_hours, 120)
        self.assertEqual(log.author, self.dispatcher)

    def test_maintenance_log_blocks_decreasing_engine_hours(self):
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=150,
            note='Базовая запись',
            author=self.dispatcher,
        )
        log = MaintenanceLog(
            ship=self.ship,
            engine_hours=149,
            note='Некорректное уменьшение',
            author=self.dispatcher,
        )

        with self.assertRaises(ValidationError):
            log.full_clean()

    def test_maintenance_log_supports_multiple_entries_per_ship(self):
        first = MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=150,
            note='Первая запись',
            author=self.dispatcher,
        )
        second = MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=175,
            note='Вторая запись',
            author=self.dispatcher,
        )

        logs = list(MaintenanceLog.objects.filter(ship=self.ship).order_by('recorded_at', 'id'))
        self.assertEqual([entry.id for entry in logs], [first.id, second.id])
        self.assertEqual(logs[-1].engine_hours, 175)

    def test_maintenance_log_allows_blank_note(self):
        log = MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=180,
            note='',
            author=self.dispatcher,
        )

        self.assertEqual(log.note, '')

    def test_captain_can_open_own_panel(self):
        captain_user = User.objects.create_user(username='captain_panel_user', password='pass12345')
        captain_user.role.role = 'captain'
        captain_user.role.save()
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )

        self.client.login(username='captain_panel_user', password='pass12345')
        response = self.client.get(reverse('fleet:captain_panel'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ship'], self.ship)

    def test_captain_can_add_valid_maintenance_log(self):
        captain_user = User.objects.create_user(username='captain_log_user', password='pass12345')
        captain_user.role.role = 'captain'
        captain_user.role.save()
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )

        self.client.login(username='captain_log_user', password='pass12345')
        response = self.client.post(
            reverse('fleet:captain_panel'),
            {
                'note': 'Появился посторонний шум в редукторе.',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        log = MaintenanceLog.objects.get(ship=self.ship, note='Появился посторонний шум в редукторе.')
        self.assertEqual(log.author, captain_user)
        self.assertEqual(log.engine_hours, 0)

        captain_notifications = Notification.objects.filter(
            entity_type='ship',
            entity_id=self.ship.id,
            code=f'captain_message_log_{log.id}',
        )
        self.assertEqual(captain_notifications.count(), 1)
        self.assertFalse(captain_notifications.first().is_read)

    def test_captain_message_uses_latest_known_engine_hours(self):
        captain_user = User.objects.create_user(username='captain_invalid_log', password='pass12345')
        captain_user.role.role = 'captain'
        captain_user.role.save()
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=220,
            note='Предыдущая запись.',
            author=self.dispatcher,
        )

        self.client.login(username='captain_invalid_log', password='pass12345')
        response = self.client.post(
            reverse('fleet:captain_panel'),
            {
                'note': 'Передаю сообщение без ручного ввода наработки.',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MaintenanceLog.objects.filter(ship=self.ship).count(), 2)
        latest_log = MaintenanceLog.objects.filter(ship=self.ship).order_by('-recorded_at', '-id').first()
        self.assertEqual(latest_log.engine_hours, 220)

    def test_captain_cannot_send_empty_message(self):
        captain_user = User.objects.create_user(username='captain_empty_message', password='pass12345')
        captain_user.role.role = 'captain'
        captain_user.role.save()
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )

        self.client.login(username='captain_empty_message', password='pass12345')
        response = self.client.post(
            reverse('fleet:captain_panel'),
            {
                'note': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MaintenanceLog.objects.filter(ship=self.ship, author=captain_user).count(), 0)

    def test_smoke_captain_engineer_director_flow(self):
        captain_user = self._create_user_with_role('smoke_captain', 'captain')
        engineer_user = self._create_user_with_role('smoke_engineer', 'engineer')
        director_user = self._create_user_with_role('smoke_director', 'director')
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )
        engine_work = RepairWork.objects.create(code='smoke_engine', name='Двигатель', base_cost=1000)

        self.client.login(username='smoke_captain', password='pass12345')
        captain_response = self.client.post(
            reverse('fleet:captain_panel'),
            {'note': 'Поломка двигателя в рейсе.'},
            follow=True,
        )
        self.assertEqual(captain_response.status_code, 200)
        self.client.logout()

        self.client.login(username='smoke_engineer', password='pass12345')
        feed_response = self.client.get(reverse('fleet:captain_messages_list'))
        self.assertEqual(feed_response.status_code, 200)
        self.assertContains(feed_response, 'Поломка двигателя в рейсе.')

        maintenance = Maintenance.objects.get(
            ship=self.ship,
            maintenance_type='emergency',
        )

        edit_response = self.client.post(
            reverse('fleet:maintenance_edit', args=[maintenance.id]),
            {
                'ship': str(self.ship.id),
                'maintenance_type': 'emergency',
                'description': 'Ремонт по сообщению капитана (уточнено)',
                'start_date': timezone.now().date().isoformat(),
                'end_date': '',
                'cost': '0',
                'status': 'in_progress',
                'contractor': '',
                'contractor_text': '',
                'repair_works': [str(engine_work.id)],
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        self.client.logout()

        self.client.login(username='smoke_director', password='pass12345')
        director_feed = self.client.get(reverse('fleet:captain_messages_list'))
        director_maintenance = self.client.get(reverse('fleet:maintenance_detail', args=[maintenance.id]))

        self.assertEqual(director_feed.status_code, 200)
        self.assertEqual(director_maintenance.status_code, 200)
        self.assertContains(director_feed, 'Поломка двигателя в рейсе.')
        self.assertContains(director_maintenance, 'Ремонт по сообщению капитана (уточнено)')

    def test_captain_cannot_view_other_ship(self):
        captain_user = User.objects.create_user(username='captain_isolation_user', password='pass12345')
        captain_user.role.role = 'captain'
        captain_user.role.save()
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )

        self.client.login(username='captain_isolation_user', password='pass12345')
        response = self.client.get(reverse('fleet:captain_panel'), {'ship_id': self.other_ship.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ship'], self.ship)

    def test_captain_can_open_only_own_maintenance_log_list(self):
        captain_user = User.objects.create_user(username='captain_log_owner', password='pass12345')
        captain_user.role.role = 'captain'
        captain_user.role.save()
        CrewMember.objects.create(
            user=captain_user,
            rank='captain',
            assigned_ship=self.ship,
        )
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=240,
            note='Контрольная запись капитана.',
            author=captain_user,
        )

        self.client.login(username='captain_log_owner', password='pass12345')
        own_response = self.client.get(reverse('fleet:maintenance_log_list', args=[self.ship.id]))
        other_response = self.client.get(reverse('fleet:maintenance_log_list', args=[self.other_ship.id]))

        self.assertEqual(own_response.status_code, 200)
        self.assertContains(own_response, 'Контрольная запись капитана.')
        self.assertEqual(other_response.status_code, 403)

    def test_engineer_can_view_captain_messages_feed(self):
        engineer_user = self._create_user_with_role('captain_feed_engineer', 'engineer')
        captain_user = self._create_user_with_role('captain_feed_author', 'captain')
        CrewMember.objects.create(user=captain_user, rank='captain', assigned_ship=self.ship)
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=240,
            note='Сообщение для общей ленты.',
            author=captain_user,
        )

        self.client.login(username='captain_feed_engineer', password='pass12345')
        response = self.client.get(reverse('fleet:captain_messages_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Сообщение для общей ленты.')
        self.assertContains(response, self.ship.name)

    def test_notification_detail_marks_captain_message_as_read(self):
        captain_user = self._create_user_with_role('captain_read_author', 'captain')
        engineer_user = self._create_user_with_role('captain_read_engineer', 'engineer')
        CrewMember.objects.create(user=captain_user, rank='captain', assigned_ship=self.ship)
        log = MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=250,
            note='Новое сообщение для уведомления.',
            author=captain_user,
        )
        notification = Notification.objects.get(
            entity_type='ship',
            entity_id=self.ship.id,
            code=f'captain_message_log_{log.id}',
        )
        self.assertFalse(notification.is_read)

        self.client.login(username='captain_read_engineer', password='pass12345')
        response = self.client.get(reverse('fleet:notification_detail', args=[notification.id]))

        self.assertEqual(response.status_code, 200)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_non_captain_cannot_access_captain_panel(self):
        self.client.login(username='crew_member_test', password='pass12345')
        response = self.client.get(reverse('fleet:captain_panel'))

        self.assertEqual(response.status_code, 403)

    def test_maintenance_log_without_keywords_does_not_create_auto_events(self):
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=300,
            note='Плановый осмотр без замечаний.',
            author=self.dispatcher,
        )

        self.assertFalse(
            Notification.objects.filter(
                entity_type='ship',
                entity_id=self.ship.id,
                code='maintenance_log_keyword_alert',
            ).exists()
        )
        self.assertFalse(
            Maintenance.objects.filter(
                ship=self.ship,
                maintenance_type='emergency',
                status__in=['planned', 'in_progress'],
            ).exists()
        )

    def test_maintenance_log_with_keyword_creates_notification_and_emergency_maintenance(self):
        log = MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=320,
            note='Обнаружена неисправность топливной системы.',
            author=self.dispatcher,
        )

        notification = Notification.objects.get(
            entity_type='ship',
            entity_id=self.ship.id,
            code='maintenance_log_keyword_alert',
        )
        self.assertEqual(notification.severity, SEVERITY_BLOCKING)
        self.assertEqual(notification.context['log_id'], log.id)
        self.assertIn('неисправность', notification.message.lower())

        maintenance = Maintenance.objects.get(
            ship=self.ship,
            maintenance_type='emergency',
        )
        self.assertEqual(maintenance.status, 'planned')
        self.assertIn(str(log.engine_hours), maintenance.description)

    def test_maintenance_log_threshold_exceedance_creates_warning_notification(self):
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=10000,
            note='Параметры в пределах рабочей нормы.',
            author=self.dispatcher,
        )

        notification = Notification.objects.get(
            entity_type='ship',
            entity_id=self.ship.id,
            code='maintenance_log_engine_hours_threshold',
        )
        self.assertEqual(notification.severity, SEVERITY_WARNING)
        self.assertEqual(notification.context['threshold'], 10000)

    def test_repeated_keyword_logs_do_not_create_duplicate_alerts_or_repairs(self):
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=350,
            note='Поломка системы охлаждения.',
            author=self.dispatcher,
        )
        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=360,
            note='Повторно: поломка все еще не устранена.',
            author=self.dispatcher,
        )

        self.assertEqual(
            Notification.objects.filter(
                entity_type='ship',
                entity_id=self.ship.id,
                code='maintenance_log_keyword_alert',
            ).count(),
            1,
        )
        self.assertEqual(
            Maintenance.objects.filter(
                ship=self.ship,
                maintenance_type='emergency',
                status__in=['planned', 'in_progress'],
            ).count(),
            1,
        )

    def test_existing_active_emergency_maintenance_prevents_duplicate_creation(self):
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='emergency',
            description='Уже созданный внеплановый ремонт.',
            start_date=timezone.now().date(),
            status='planned',
        )

        MaintenanceLog.objects.create(
            ship=self.ship,
            engine_hours=370,
            note='Авария рулевого привода.',
            author=self.dispatcher,
        )

        self.assertEqual(
            Maintenance.objects.filter(
                ship=self.ship,
                maintenance_type='emergency',
                status__in=['planned', 'in_progress'],
            ).count(),
            1,
        )

    def test_ship_actual_status_uses_open_ended_maintenance(self):
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Диагностика двигателя',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            end_date=None,
            status='in_progress',
        )

        self.assertEqual(self.ship.get_actual_status(), 'under_repair')

    def test_readiness_blocks_when_no_crew_assigned(self):
        result = ship_readiness_service(self.other_ship)

        self.assertFalse(result.is_ready)
        self.assertIn('Не назначен ни один член экипажа.', result.blocking_reasons)

    def test_readiness_service_returns_unified_business_check_result(self):
        result = ship_readiness_service(self.other_ship)

        self.assertIsInstance(result, BusinessCheckResult)
        self.assertFalse(result.is_ok)
        self.assertEqual(result.is_ok, result.is_ready)
        self.assertEqual(result.is_ok, result.is_valid)
        self.assertEqual(result.severity_summary[SEVERITY_BLOCKING], 1)
        self.assertEqual(result.severity_summary[SEVERITY_WARNING], 0)
        self.assertEqual(result.context['ship_id'], self.other_ship.id)

    def test_is_ok_is_canonical_and_aliases_are_compatible(self):
        result = BusinessCheckResult()

        self.assertTrue(result.is_ok)
        self.assertTrue(result.is_ready)
        self.assertTrue(result.is_valid)

        result.add_blocking('blocking issue')

        self.assertFalse(result.is_ok)
        self.assertEqual(result.is_ok, result.is_ready)
        self.assertEqual(result.is_ok, result.is_valid)

    def test_readiness_blocks_when_minimum_crew_not_complete(self):
        result = ship_readiness_service(self.ship, crew_members=[self.seaman])

        self.assertFalse(result.is_ready)
        self.assertTrue(any('не хватает членов экипажа' in reason.lower() for reason in result.blocking_reasons))
        for role_name in MINIMUM_READY_CREW_ROLES.values():
            self.assertTrue(any(role_name in reason for reason in result.blocking_reasons))

    def test_readiness_blocks_expired_required_contract(self):
        self._create_minimum_required_crew(self.ship, expired_captain=True)

        result = ship_readiness_service(self.ship)

        self.assertFalse(result.is_ready)
        self.assertTrue(any('не хватает членов экипажа' in reason.lower() for reason in result.blocking_reasons))
        self.assertTrue(any('истёк контракт' in warning.lower() for warning in result.warnings))

    def test_readiness_passes_when_ship_meets_configured_crew_requirements(self):
        crew_members = self._create_minimum_required_crew(self.ship)

        result = ship_readiness_service(self.ship, crew_members=crew_members)

        self.assertTrue(result.is_ready)
        self.assertEqual(result.blocking_reasons, [])
        self.assertTrue(result.context['crew_requirements']['requirements_configured'])
        self.assertEqual(len(result.context['crew_requirements']['missing_roles']), 0)

    def test_readiness_blocks_when_required_role_is_missing_by_ship_type(self):
        engineer = self._create_named_crew_member(
            self.ship,
            'engineer',
            'engineer_only_missing_captain',
            timezone.now().date() + timezone.timedelta(days=30),
        )
        first_mate = self._create_named_crew_member(
            self.ship,
            'first_mate',
            'mate_only_missing_captain',
            timezone.now().date() + timezone.timedelta(days=30),
        )

        result = ship_readiness_service(self.ship, crew_members=[engineer, first_mate])

        self.assertFalse(result.is_ready)
        self.assertTrue(any('Капитан' in reason for reason in result.blocking_reasons))
        self.assertEqual(result.context['crew_requirements']['missing_roles'][0]['role'], 'captain')

    def test_readiness_blocks_when_required_role_count_is_insufficient(self):
        ShipCrewRequirement.objects.update_or_create(
            ship_type='fishing',
            role='seaman',
            defaults={'required_count': 2},
        )
        crew_members = self._create_minimum_required_crew(self.ship)
        crew_members.append(
            self._create_named_crew_member(
                self.ship,
                'seaman',
                'single_required_seaman',
                timezone.now().date() + timezone.timedelta(days=30),
            )
        )

        result = ship_readiness_service(self.ship, crew_members=crew_members)

        self.assertFalse(result.is_ready)
        self.assertTrue(any('Матрос' in reason for reason in result.blocking_reasons))
        seaman_requirement = next(
            item for item in result.context['crew_requirements']['requirements']
            if item['role'] == 'seaman'
        )
        self.assertEqual(seaman_requirement['required_count'], 2)
        self.assertEqual(seaman_requirement['eligible_count'], 1)
        self.assertEqual(seaman_requirement['missing_count'], 1)

    def test_readiness_warns_when_ship_type_has_no_crew_requirements(self):
        passenger_ship = Ship.objects.create(
            name='Пассажир',
            imo_number='IMO1000099',
            type='passenger',
        )
        passenger_crew = self._create_named_crew_member(
            passenger_ship,
            'seaman',
            'passenger_seaman',
            timezone.now().date() + timezone.timedelta(days=30),
        )

        result = ship_readiness_service(passenger_ship, crew_members=[passenger_crew])

        self.assertTrue(result.is_ready)
        self.assertTrue(any('не настроены требования' in warning.lower() for warning in result.warnings))
        self.assertFalse(result.context['crew_requirements']['requirements_configured'])

    def test_readiness_returns_requirement_context_for_ui(self):
        ShipCrewRequirement.objects.update_or_create(
            ship_type='fishing',
            role='seaman',
            defaults={'required_count': 2},
        )
        crew_members = self._create_minimum_required_crew(self.ship)
        crew_members.append(
            self._create_named_crew_member(
                self.ship,
                'seaman',
                'context_seaman',
                timezone.now().date() + timezone.timedelta(days=30),
            )
        )

        result = ship_readiness_service(self.ship, crew_members=crew_members)
        crew_context = result.context['crew_requirements']

        self.assertIn('requirements', crew_context)
        self.assertIn('actual_counts', crew_context)
        self.assertIn('eligible_counts', crew_context)
        self.assertIn('missing_roles', crew_context)
        self.assertEqual(crew_context['ship_type'], 'fishing')

    def test_readiness_handles_multiple_role_requirements_at_once(self):
        ShipCrewRequirement.objects.update_or_create(
            ship_type='fishing',
            role='seaman',
            defaults={'required_count': 2},
        )
        crew_members = [
            self._create_named_crew_member(
                self.ship,
                'captain',
                'multi_role_captain',
                timezone.now().date() + timezone.timedelta(days=30),
            ),
            self._create_named_crew_member(
                self.ship,
                'seaman',
                'multi_role_seaman',
                timezone.now().date() + timezone.timedelta(days=30),
            ),
        ]

        result = ship_readiness_service(self.ship, crew_members=crew_members)

        self.assertFalse(result.is_ready)
        self.assertEqual(len(result.context['crew_requirements']['missing_roles']), 3)
        missing_roles = [item['role'] for item in result.context['crew_requirements']['missing_roles']]
        self.assertEqual(missing_roles, ['engineer', 'first_mate', 'seaman'])

    def test_readiness_eligible_counts_exclude_expired_contracts(self):
        valid_captain = self._create_named_crew_member(
            self.ship,
            'captain',
            'eligible_valid_captain',
            timezone.now().date() + timezone.timedelta(days=30),
        )
        expired_captain = self._create_named_crew_member(
            self.ship,
            'captain',
            'eligible_expired_captain',
            timezone.now().date() - timezone.timedelta(days=1),
        )
        first_mate = self._create_named_crew_member(
            self.ship,
            'first_mate',
            'eligible_first_mate',
            timezone.now().date() + timezone.timedelta(days=30),
        )
        engineer = self._create_named_crew_member(
            self.ship,
            'engineer',
            'eligible_engineer',
            timezone.now().date() + timezone.timedelta(days=30),
        )

        result = ship_readiness_service(
            self.ship,
            crew_members=[valid_captain, expired_captain, first_mate, engineer],
        )

        self.assertTrue(result.is_ready)
        captain_requirement = next(
            item for item in result.context['crew_requirements']['requirements']
            if item['role'] == 'captain'
        )
        self.assertEqual(captain_requirement['actual_count'], 2)
        self.assertEqual(captain_requirement['eligible_count'], 1)
        self.assertTrue(any('истёк контракт' in warning.lower() for warning in result.warnings))

    def test_readiness_counts_members_without_contract_as_actual_and_eligible_with_warning(self):
        captain = self._create_named_crew_member(
            self.ship,
            'captain',
            'no_contract_captain',
            None,
        )
        first_mate = self._create_named_crew_member(
            self.ship,
            'first_mate',
            'no_contract_first_mate',
            timezone.now().date() + timezone.timedelta(days=30),
        )
        engineer = self._create_named_crew_member(
            self.ship,
            'engineer',
            'no_contract_engineer',
            timezone.now().date() + timezone.timedelta(days=30),
        )

        result = ship_readiness_service(self.ship, crew_members=[captain, first_mate, engineer])

        self.assertTrue(result.is_ready)
        captain_requirement = next(
            item for item in result.context['crew_requirements']['requirements']
            if item['role'] == 'captain'
        )
        self.assertEqual(captain_requirement['actual_count'], 1)
        self.assertEqual(captain_requirement['eligible_count'], 1)
        self.assertTrue(any('не заполнена дата окончания контракта' in warning.lower() for warning in result.warnings))

    def test_readiness_missing_roles_do_not_duplicate_entries(self):
        result = ship_readiness_service(self.ship, crew_members=[self.seaman])

        missing_roles = result.context['crew_requirements']['missing_roles']
        self.assertEqual(len(missing_roles), len({item['role'] for item in missing_roles}))

    def test_readiness_with_empty_crew_and_configured_requirements_is_stable(self):
        result = ship_readiness_service(self.ship, crew_members=[])

        self.assertFalse(result.is_ready)
        self.assertEqual(result.blocking_reasons, ['Не назначен ни один член экипажа.'])
        self.assertTrue(result.context['crew_requirements']['requirements_configured'])
        self.assertEqual(
            [item['role'] for item in result.context['crew_requirements']['missing_roles']],
            ['captain', 'engineer', 'first_mate'],
        )

    def test_validate_crew_for_voyage_period_blocks_if_contract_expired_before_start(self):
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'expired_before_start',
            timezone.now().date() - timezone.timedelta(days=1),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=timezone.now() + timezone.timedelta(days=2),
            planned_return_date=timezone.now() + timezone.timedelta(days=5),
        )

        self.assertFalse(result.is_valid)
        self.assertTrue(any('истёк до начала рейса' in reason for reason in result.blocking_reasons))

    def test_validate_crew_for_voyage_period_blocks_if_contract_ends_during_voyage(self):
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'ends_during_voyage',
            (timezone.now() + timezone.timedelta(days=4)).date(),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=timezone.now() + timezone.timedelta(days=2),
            planned_return_date=timezone.now() + timezone.timedelta(days=6),
        )

        self.assertFalse(result.is_valid)
        self.assertTrue(any('раньше планового завершения рейса' in reason for reason in result.blocking_reasons))

    def test_validate_crew_for_voyage_period_warns_if_contract_missing(self):
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'missing_contract',
            None,
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=timezone.now() + timezone.timedelta(days=2),
            planned_return_date=timezone.now() + timezone.timedelta(days=6),
        )

        self.assertTrue(result.is_valid)
        self.assertTrue(any('не указана дата окончания контракта' in warning for warning in result.warnings))

    def test_contract_validation_returns_unified_business_check_result(self):
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'service_format_member',
            None,
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=timezone.now() + timezone.timedelta(days=2),
            planned_return_date=timezone.now() + timezone.timedelta(days=6),
        )

        self.assertIsInstance(result, BusinessCheckResult)
        self.assertTrue(result.is_ok)
        self.assertEqual(result.is_ok, result.is_ready)
        self.assertEqual(result.is_ok, result.is_valid)
        self.assertEqual(result.severity_summary[SEVERITY_BLOCKING], 0)
        self.assertEqual(result.severity_summary[SEVERITY_WARNING], 1)
        self.assertIn('voyage_start_date', result.context)
        self.assertIn('planned_return_date', result.context)

    def test_validate_crew_for_voyage_period_allows_empty_crew(self):
        result = validate_crew_for_voyage_period(
            [],
            voyage_start_date=timezone.now() + timezone.timedelta(days=2),
            planned_return_date=timezone.now() + timezone.timedelta(days=6),
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.blocking_reasons, [])
        self.assertEqual(result.warnings, [])

    def test_validate_crew_for_voyage_period_blocks_when_contract_ends_on_voyage_start(self):
        start_date = timezone.now() + timezone.timedelta(days=2)
        end_date = timezone.now() + timezone.timedelta(days=6)
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'ends_on_start',
            start_date.date(),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=start_date,
            planned_return_date=end_date,
        )

        self.assertFalse(result.is_valid)
        self.assertTrue(any('раньше планового завершения рейса' in reason for reason in result.blocking_reasons))

    def test_validate_crew_for_voyage_period_warns_when_contract_ends_on_voyage_end(self):
        start_date = timezone.now() + timezone.timedelta(days=2)
        end_date = timezone.now() + timezone.timedelta(days=6)
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'ends_on_end',
            end_date.date(),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=start_date,
            planned_return_date=end_date,
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.blocking_reasons, [])
        self.assertTrue(any('вскоре после рейса' in warning for warning in result.warnings))

    def test_validate_crew_for_voyage_period_warns_when_contract_ends_day_after_voyage_end(self):
        start_date = timezone.now() + timezone.timedelta(days=2)
        end_date = timezone.now() + timezone.timedelta(days=6)
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'ends_after_end',
            (end_date + timezone.timedelta(days=1)).date(),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=start_date,
            planned_return_date=end_date,
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.blocking_reasons, [])
        self.assertTrue(any('вскоре после рейса' in warning for warning in result.warnings))

    def test_validate_crew_for_voyage_period_allows_missing_return_date(self):
        start_date = timezone.now() + timezone.timedelta(days=2)
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'draft_without_return',
            start_date.date(),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=start_date,
            planned_return_date=None,
        )

        self.assertTrue(result.is_valid)
        self.assertEqual(result.blocking_reasons, [])

    def test_validate_crew_for_voyage_period_warns_if_contract_ends_soon_after_voyage(self):
        member = self._create_named_crew_member(
            self.ship,
            'engineer',
            'expires_after_voyage',
            (timezone.now() + timezone.timedelta(days=8)).date(),
        )

        result = validate_crew_for_voyage_period(
            [member],
            voyage_start_date=timezone.now() + timezone.timedelta(days=2),
            planned_return_date=timezone.now() + timezone.timedelta(days=3),
        )

        self.assertTrue(result.is_valid)
        self.assertTrue(any('вскоре после рейса' in warning for warning in result.warnings))

    def test_operational_state_prefers_maintenance_over_active_voyage(self):
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=1),
            end_date=None,
            fishing_area='bering',
            is_completed=False,
        )
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Аварийный ремонт',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            end_date=None,
            status='in_progress',
        )

        state = get_ship_operational_state(self.ship)

        self.assertEqual(state.actual_status, 'under_repair')

    def test_operational_state_uses_safe_location_without_none(self):
        self.ship.current_location = 'None'
        self.ship.save(update_fields=['current_location'])

        state = get_ship_operational_state(self.ship)

        self.assertEqual(state.location_display, 'Порт не указан')

    def test_evaluate_voyage_health_marks_overdue_as_blocking(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=5),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_completed=False,
        )

        health = evaluate_voyage_health(voyage)

        self.assertEqual(health.status, 'overdue')
        self.assertEqual(health.severity, SEVERITY_BLOCKING)
        self.assertEqual(health.code, 'voyage_overdue')

    def test_evaluate_voyage_health_marks_too_long_as_warning(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=None,
            is_completed=False,
        )

        health = evaluate_voyage_health(voyage)

        self.assertEqual(health.status, 'too_long')
        self.assertEqual(health.severity, SEVERITY_WARNING)
        self.assertEqual(health.code, 'voyage_too_long')

    def test_evaluate_voyage_health_marks_normal_voyage_as_normal(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=2),
            is_completed=False,
        )

        health = evaluate_voyage_health(voyage)

        self.assertEqual(health.status, 'normal')
        self.assertIsNone(health.severity)
        self.assertIsNone(health.code)

    def test_evaluate_maintenance_health_marks_overdue_start_as_blocking(self):
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Просроченный старт',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            status='planned',
        )

        health = evaluate_maintenance_health(maintenance)

        self.assertEqual(health.status, 'overdue_start')
        self.assertEqual(health.severity, SEVERITY_BLOCKING)
        self.assertEqual(health.code, 'maintenance_overdue_start')

    def test_evaluate_maintenance_health_marks_too_long_as_warning(self):
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Долгий ремонт',
            start_date=timezone.now().date() - timezone.timedelta(days=31),
            status='in_progress',
        )

        health = evaluate_maintenance_health(maintenance)

        self.assertEqual(health.status, 'too_long')
        self.assertEqual(health.severity, SEVERITY_WARNING)
        self.assertEqual(health.code, 'maintenance_too_long')

    def test_evaluate_maintenance_health_marks_normal_maintenance_as_normal(self):
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Нормальный ремонт',
            start_date=timezone.now().date(),
            status='in_progress',
        )

        health = evaluate_maintenance_health(maintenance)

        self.assertEqual(health.status, 'normal')
        self.assertIsNone(health.severity)
        self.assertIsNone(health.code)

    def test_voyage_health_filters_follow_canonical_rules(self):
        overdue_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_completed=False,
        )
        too_long_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() + timezone.timedelta(days=1),
            is_completed=False,
        )
        normal_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=2),
            is_completed=False,
        )

        overdue_ids = set(
            filter_voyages_by_health(Voyage.objects.all(), health_filter='overdue').values_list('id', flat=True)
        )
        too_long_ids = set(
            filter_voyages_by_health(Voyage.objects.all(), health_filter='too_long').values_list('id', flat=True)
        )
        normal_ids = set(
            filter_voyages_by_health(Voyage.objects.all(), health_filter='normal').values_list('id', flat=True)
        )

        self.assertEqual(overdue_ids, {overdue_voyage.id})
        self.assertEqual(too_long_ids, {too_long_voyage.id})
        self.assertEqual(normal_ids, {normal_voyage.id})

    def test_voyage_health_priority_uses_canonical_rule_order(self):
        overdue_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_completed=False,
        )
        too_long_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() + timezone.timedelta(days=1),
            is_completed=False,
        )
        normal_voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=2),
            is_completed=False,
        )

        prioritized_ids = list(
            annotate_voyage_health_priority(Voyage.objects.all())
            .order_by('criticality', 'id')
            .values_list('id', flat=True)
        )

        self.assertEqual(prioritized_ids, [overdue_voyage.id, too_long_voyage.id, normal_voyage.id])

    def test_maintenance_health_filters_follow_canonical_rules(self):
        overdue_maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Просроченный старт',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            status='planned',
        )
        too_long_maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Долгий ремонт',
            start_date=timezone.now().date() - timezone.timedelta(days=31),
            status='in_progress',
        )
        normal_maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Нормальный ремонт',
            start_date=timezone.now().date(),
            status='in_progress',
        )

        overdue_ids = set(
            filter_maintenances_by_health(Maintenance.objects.all(), health_filter='overdue').values_list('id', flat=True)
        )
        too_long_ids = set(
            filter_maintenances_by_health(Maintenance.objects.all(), health_filter='too_long').values_list('id', flat=True)
        )
        normal_ids = set(
            filter_maintenances_by_health(Maintenance.objects.all(), health_filter='normal').values_list('id', flat=True)
        )

        self.assertEqual(overdue_ids, {overdue_maintenance.id})
        self.assertEqual(too_long_ids, {too_long_maintenance.id})
        self.assertEqual(normal_ids, {normal_maintenance.id})

    def test_maintenance_health_priority_uses_canonical_rule_order(self):
        overdue_maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Просроченный старт',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            status='planned',
        )
        too_long_maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Долгий ремонт',
            start_date=timezone.now().date() - timezone.timedelta(days=31),
            status='in_progress',
        )
        normal_maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Нормальный ремонт',
            start_date=timezone.now().date(),
            status='in_progress',
        )

        prioritized_ids = list(
            annotate_maintenance_health_priority(Maintenance.objects.all())
            .order_by('criticality', 'id')
            .values_list('id', flat=True)
        )

        self.assertEqual(
            prioritized_ids,
            [overdue_maintenance.id, too_long_maintenance.id, normal_maintenance.id],
        )

    def test_voyage_create_does_not_persist_when_crew_is_busy(self):
        minimum_crew = self._create_minimum_required_crew(self.ship)
        existing_voyage = Voyage.objects.create(
            ship=self.other_ship,
            start_date=timezone.now() - timezone.timedelta(days=1),
            end_date=None,
            is_completed=False,
        )
        existing_voyage.crew.add(minimum_crew[0])

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_create'),
            {
                'ship': str(self.ship.id),
                'start_date': (timezone.now() + timezone.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': '',
                'fishing_area': 'okhotsk',
                'catch_plan': '12.5',
                'crew': [str(member.id) for member in minimum_crew],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Voyage.objects.filter(ship=self.ship).count(), 0)

    def test_voyage_create_uses_readiness_service(self):
        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_create'),
            {
                'ship': str(self.other_ship.id),
                'start_date': (timezone.now() + timezone.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': '',
                'fishing_area': 'okhotsk',
                'catch_plan': '12.5',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Voyage.objects.filter(ship=self.other_ship).count(), 0)

    def test_voyage_create_blocks_if_selected_crew_contract_ends_during_voyage(self):
        minimum_crew = self._create_minimum_required_crew(self.ship)
        minimum_crew[2].contract_end_date = (timezone.now() + timezone.timedelta(days=4)).date()
        minimum_crew[2].save(update_fields=['contract_end_date'])

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_create'),
            {
                'ship': str(self.ship.id),
                'start_date': (timezone.now() + timezone.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': (timezone.now() + timezone.timedelta(days=6)).strftime('%Y-%m-%dT%H:%M'),
                'fishing_area': 'okhotsk',
                'catch_plan': '12.5',
                'crew': [str(member.id) for member in minimum_crew],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Voyage.objects.filter(ship=self.ship).count(), 0)

    def test_voyage_create_succeeds_with_valid_contracts(self):
        minimum_crew = self._create_minimum_required_crew(self.ship)

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_create'),
            {
                'ship': str(self.ship.id),
                'start_date': (timezone.now() + timezone.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': (timezone.now() + timezone.timedelta(days=4)).strftime('%Y-%m-%dT%H:%M'),
                'fishing_area': 'okhotsk',
                'catch_plan': '12.5',
                'crew': [str(member.id) for member in minimum_crew],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Voyage.objects.filter(ship=self.ship).count(), 1)

    def test_voyage_edit_uses_readiness_service(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() + timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=5),
            is_completed=False,
        )

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_edit', args=[voyage.id]),
            {
                'ship': str(self.other_ship.id),
                'start_date': (timezone.now() + timezone.timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': (timezone.now() + timezone.timedelta(days=6)).strftime('%Y-%m-%dT%H:%M'),
                'fishing_area': 'okhotsk',
                'catch_plan': '18.0',
                'crew': [],
            },
        )

        self.assertEqual(response.status_code, 302)
        voyage.refresh_from_db()
        self.assertEqual(voyage.ship_id, self.ship.id)

    def test_voyage_edit_blocks_if_selected_crew_contract_ends_during_voyage(self):
        minimum_crew = self._create_minimum_required_crew(self.ship)
        minimum_crew[1].contract_end_date = (timezone.now() + timezone.timedelta(days=4)).date()
        minimum_crew[1].save(update_fields=['contract_end_date'])
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() + timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=5),
            is_completed=False,
        )

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_edit', args=[voyage.id]),
            {
                'ship': str(self.ship.id),
                'start_date': (timezone.now() + timezone.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': (timezone.now() + timezone.timedelta(days=6)).strftime('%Y-%m-%dT%H:%M'),
                'fishing_area': 'okhotsk',
                'catch_plan': '18.0',
                'crew': [str(member.id) for member in minimum_crew],
            },
        )

        self.assertEqual(response.status_code, 302)
        voyage.refresh_from_db()
        self.assertEqual(voyage.end_date.date(), (timezone.now() + timezone.timedelta(days=5)).date())

    def test_voyage_edit_succeeds_without_changing_valid_crew(self):
        minimum_crew = self._create_minimum_required_crew(self.ship)
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() + timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=5),
            is_completed=False,
        )
        voyage.crew.set(minimum_crew)

        self.client.force_login(self.dispatcher)
        response = self.client.post(
            reverse('fleet:voyage_edit', args=[voyage.id]),
            {
                'ship': str(self.ship.id),
                'start_date': (timezone.now() + timezone.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
                'end_date': (timezone.now() + timezone.timedelta(days=5)).strftime('%Y-%m-%dT%H:%M'),
                'fishing_area': 'bering',
                'catch_plan': '18.0',
                'crew': [str(member.id) for member in minimum_crew],
            },
        )

        self.assertEqual(response.status_code, 302)
        voyage.refresh_from_db()
        self.assertEqual(voyage.fishing_area, 'bering')

    def test_voyage_complete_saves_actual_catch_and_end_date(self):
        self._create_minimum_required_crew(self.ship)
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=3),
            end_date=None,
            is_completed=False,
        )

        self.client.force_login(self.dispatcher)
        end_date = timezone.now().replace(second=0, microsecond=0)
        response = self.client.post(
            reverse('fleet:voyage_complete', args=[voyage.id]),
            {
                'actual_catch': '17.4',
                'end_date': end_date.strftime('%Y-%m-%dT%H:%M'),
            },
        )

        self.assertEqual(response.status_code, 302)
        voyage.refresh_from_db()
        self.assertTrue(voyage.is_completed)
        self.assertEqual(voyage.actual_catch, 17.4)
        self.assertEqual(voyage.end_date.replace(second=0, microsecond=0), end_date)

    def test_system_problem_states_collect_supported_problem_types(self):
        self._create_minimum_required_crew(self.ship)
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='current',
            description='Длительный ремонт',
            start_date=timezone.now().date() - timezone.timedelta(days=31),
            end_date=None,
            status='in_progress',
        )
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=None,
            fishing_area='okhotsk',
            is_completed=False,
        )
        self.seaman.contract_end_date = timezone.now().date() - timezone.timedelta(days=1)
        self.seaman.save(update_fields=['contract_end_date'])

        result = get_system_problem_states()

        self.assertGreaterEqual(result.total_issues, 4)
        self.assertTrue(result.grouped['ships'])
        self.assertTrue(result.grouped['voyages'])
        self.assertTrue(result.grouped['maintenances'])
        self.assertTrue(result.grouped['crew'])
        self.assertGreaterEqual(result.severity_summary[SEVERITY_BLOCKING], 2)
        self.assertGreaterEqual(result.severity_summary[SEVERITY_WARNING], 2)

    def test_system_problem_states_use_stable_operational_health_codes(self):
        maintenance = Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Просроченный ремонт',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            status='planned',
        )
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_completed=False,
        )

        result = get_system_problem_states()
        maintenance_codes = {
            issue.code for issue in result.issues
            if issue.category == 'maintenances' and issue.object.id == maintenance.id
        }
        voyage_codes = {
            issue.code for issue in result.issues
            if issue.category == 'voyages' and issue.object.id == voyage.id
        }

        self.assertEqual(maintenance_codes, {'maintenance_overdue_start'})
        self.assertEqual(voyage_codes, {'voyage_overdue'})

    def test_system_problem_states_deduplicate_multiple_reasons_for_same_voyage(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        result = get_system_problem_states()

        self.assertEqual(
            len(
                [
                    issue for issue in result.issues
                    if issue.category == 'voyages' and issue.object.id == voyage.id
                ]
            ),
            1,
        )
        self.assertEqual(len(result.grouped['voyages']), 1)
        aggregated_issue = result.grouped['voyages'][0]
        self.assertEqual(aggregated_issue.object.id, voyage.id)
        self.assertEqual(aggregated_issue.severity, SEVERITY_BLOCKING)
        self.assertIn('просрочен', aggregated_issue.reason.lower())
        self.assertIn('Плановая дата возвращения:', aggregated_issue.details)

    def test_group_problem_issues_by_code_uses_canonical_operational_codes(self):
        Maintenance.objects.create(
            ship=self.ship,
            maintenance_type='planned',
            description='Просроченный старт',
            start_date=timezone.now().date() - timezone.timedelta(days=1),
            status='planned',
        )
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() + timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        result = get_system_problem_states()

        voyage_groups = group_problem_issues_by_code(result.issues, 'voyages')
        maintenance_groups = group_problem_issues_by_code(result.issues, 'maintenances')

        self.assertEqual(set(voyage_groups), {'voyage_too_long'})
        self.assertEqual(set(maintenance_groups), {'maintenance_overdue_start'})
        self.assertEqual(voyage_groups['voyage_too_long'][0].severity, SEVERITY_WARNING)
        self.assertEqual(maintenance_groups['maintenance_overdue_start'][0].severity, SEVERITY_BLOCKING)

    def test_system_problem_states_total_matches_grouped_lengths(self):
        self._create_minimum_required_crew(self.ship, expired_captain=True)
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )
        self.seaman.contract_end_date = timezone.now().date() + timezone.timedelta(days=2)
        self.seaman.save(update_fields=['contract_end_date'])

        result = get_system_problem_states()
        grouped_total = sum(len(items) for items in result.grouped.values())

        self.assertEqual(result.total_issues, grouped_total)
        self.assertEqual(
            result.total_issues,
            result.severity_summary[SEVERITY_BLOCKING]
            + result.severity_summary[SEVERITY_WARNING]
            + result.severity_summary[SEVERITY_INFO],
        )

    def test_dashboard_handles_no_problems(self):
        CrewMember.objects.all().delete()
        Ship.objects.all().delete()
        clean_ship = Ship.objects.create(
            name='Готовый',
            imo_number='IMO2000001',
            type='fishing',
        )
        self._create_minimum_required_crew(clean_ship)

        self.client.force_login(self.dispatcher)
        response = self.client.get(reverse('fleet:fleet_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['problematic_total'], 0)
        self.assertEqual(response.context['problematic_summary'][SEVERITY_BLOCKING], 0)
        self.assertEqual(response.context['problematic_summary'][SEVERITY_WARNING], 0)
        self.assertEqual(response.context['problematic_summary'][SEVERITY_INFO], 0)

    def test_dashboard_uses_aggregated_problematic_context(self):
        Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        self.client.force_login(self.dispatcher)
        response = self.client.get(reverse('fleet:fleet_dashboard'))

        self.assertEqual(response.status_code, 200)
        grouped_total = sum(len(items) for items in response.context['problematic'].values())
        self.assertEqual(response.context['problematic_total'], grouped_total)
        self.assertEqual(len(response.context['problematic']['voyages']), 1)

    def test_sync_notifications_creates_notifications_from_problem_states(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        sync_notifications_from_problem_states()

        notification = Notification.objects.get(
            entity_type='voyage',
            entity_id=voyage.id,
            code='voyage_overdue',
        )
        self.assertEqual(notification.severity, SEVERITY_BLOCKING)
        self.assertIn('Рейс просрочен', notification.title)
        self.assertFalse(notification.is_resolved)

    def test_sync_notifications_does_not_create_duplicates(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        sync_notifications_from_problem_states()
        sync_notifications_from_problem_states()

        self.assertEqual(
            Notification.objects.filter(
                entity_type='voyage',
                entity_id=voyage.id,
                code='voyage_overdue',
            ).count(),
            1,
        )

    def test_sync_notifications_updates_existing_notification(self):
        self.seaman.contract_end_date = timezone.now().date() + timezone.timedelta(days=5)
        self.seaman.save(update_fields=['contract_end_date'])

        sync_notifications_from_problem_states()
        notification = Notification.objects.get(
            entity_type='crew',
            entity_id=self.seaman.id,
            code='crew_contract_expiring_soon',
        )
        first_message = notification.message

        self.seaman.contract_end_date = timezone.now().date() + timezone.timedelta(days=2)
        self.seaman.save(update_fields=['contract_end_date'])

        sync_notifications_from_problem_states()
        notification.refresh_from_db()

        self.assertEqual(notification.severity, SEVERITY_WARNING)
        self.assertNotEqual(notification.message, first_message)
        self.assertIn('2', notification.message)

    def test_sync_notifications_marks_resolved_when_problem_disappears(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        sync_notifications_from_problem_states()
        notification = Notification.objects.get(
            entity_type='voyage',
            entity_id=voyage.id,
            code='voyage_overdue',
        )
        self.assertFalse(notification.is_resolved)

        voyage.is_completed = True
        voyage.save(update_fields=['is_completed'])

        sync_notifications_from_problem_states()
        notification.refresh_from_db()

        self.assertTrue(notification.is_resolved)
        self.assertIsNotNone(notification.resolved_at)

    def test_sync_notifications_maps_entity_and_severity_correctly(self):
        self.seaman.contract_end_date = timezone.now().date() - timezone.timedelta(days=1)
        self.seaman.save(update_fields=['contract_end_date'])

        sync_notifications_from_problem_states()

        notification = Notification.objects.get(
            entity_type='crew',
            entity_id=self.seaman.id,
            code='crew_contract_expired',
        )
        self.assertEqual(notification.severity, SEVERITY_BLOCKING)
        self.assertEqual(notification.context['category'], 'crew')
        self.assertEqual(notification.context['code'], 'crew_contract_expired')

    def test_sync_notifications_reactivates_resolved_notification(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        sync_notifications_from_problem_states()
        notification = Notification.objects.get(
            entity_type='voyage',
            entity_id=voyage.id,
            code='voyage_overdue',
        )

        voyage.is_completed = True
        voyage.save(update_fields=['is_completed'])
        sync_notifications_from_problem_states()
        notification.refresh_from_db()
        self.assertTrue(notification.is_resolved)
        resolved_at = notification.resolved_at

        voyage.is_completed = False
        voyage.save(update_fields=['is_completed'])
        sync_notifications_from_problem_states()
        notification.refresh_from_db()

        self.assertFalse(notification.is_resolved)
        self.assertIsNone(notification.resolved_at)
        self.assertEqual(
            Notification.objects.filter(
                entity_type='voyage',
                entity_id=voyage.id,
                code='voyage_overdue',
            ).count(),
            1,
        )
        self.assertIsNotNone(resolved_at)

    def test_sync_notifications_create_multiple_codes_for_same_entity(self):
        voyage = Voyage.objects.create(
            ship=self.ship,
            start_date=timezone.now() - timezone.timedelta(days=61),
            end_date=timezone.now() - timezone.timedelta(days=1),
            fishing_area='okhotsk',
            is_completed=False,
        )

        sync_notifications_from_problem_states()

        codes = set(
            Notification.objects.filter(
                entity_type='voyage',
                entity_id=voyage.id,
            ).values_list('code', flat=True)
        )
        self.assertEqual(codes, {'voyage_overdue'})

    def test_sync_notifications_is_stable_on_two_identical_runs(self):
        self.seaman.contract_end_date = timezone.now().date() + timezone.timedelta(days=2)
        self.seaman.save(update_fields=['contract_end_date'])

        sync_notifications_from_problem_states()
        first_snapshot = list(
            Notification.objects.order_by('id').values(
                'entity_type',
                'entity_id',
                'code',
                'severity',
                'is_resolved',
                'resolved_at',
                'message',
            )
        )

        sync_notifications_from_problem_states()
        second_snapshot = list(
            Notification.objects.order_by('id').values(
                'entity_type',
                'entity_id',
                'code',
                'severity',
                'is_resolved',
                'resolved_at',
                'message',
            )
        )

        self.assertEqual(first_snapshot, second_snapshot)
