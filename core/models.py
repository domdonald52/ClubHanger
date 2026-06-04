from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.text import slugify
from datetime import date, datetime, time, timedelta


# ============================================================================
# AUTH & ORGANIZATION
# ============================================================================

class User(AbstractUser):
    """
    Authentication only. Personal details live on ClubMember.
    Inherits: username, email, password, first_name, last_name, is_active, date_joined, etc.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f"{self.get_full_name()} ({self.username})"


class Club(models.Model):
    """
    Top-level organization. Each club manages its own members, aircraft, bookings.
    """
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(unique=True)
    
    # Contact & operational
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    
    # Operational settings
    timezone = models.CharField(max_length=50, default='Pacific/Auckland')
    currency = models.CharField(max_length=3, default='NZD')
    
    # Membership
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class ClubConfig(models.Model):
    """Club operational configuration."""
    club = models.OneToOneField(Club, on_delete=models.CASCADE, related_name='config')
    
    # Booking defaults
    default_booking_duration = models.IntegerField(default=90, help_text="Minutes")
    time_slot_interval = models.IntegerField(default=30, help_text="Minutes")
    duration_options = models.CharField(
        max_length=200, default="60,90,120,180",
        help_text="Comma-separated minutes offered in the booking/search duration picker"
    )

    def duration_choices(self):
        """Parsed, sorted, de-duplicated list of duration options (ints)."""
        out = []
        for part in (self.duration_options or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        if self.default_booking_duration not in out:
            out.append(self.default_booking_duration)
        return sorted(set(out))
    
    # Operating hours (the full window the calendar spans)
    operating_hours_start = models.TimeField(default='07:00')
    operating_hours_end = models.TimeField(default='21:00')

    # Typical flying hours (bookings outside this range require confirmation)
    typical_hours_start = models.TimeField(default='08:30')
    typical_hours_end = models.TimeField(default='17:00')

    # Theme colours (hex). A cohesive aviation palette by default.
    theme_banner = models.CharField(max_length=7, default='#1d3a5f', help_text="Top banner background")
    theme_primary = models.CharField(max_length=7, default='#2f7dd1', help_text="Buttons, links, active controls")
    theme_accent = models.CharField(max_length=7, default='#e8943a', help_text="Accent / highlights")
    theme_confirmed = models.CharField(max_length=7, default='#2f9e44', help_text="Confirmed booking pills")
    theme_pending = models.CharField(max_length=7, default='#f08c00', help_text="Pending booking pills")
    theme_weekend = models.CharField(max_length=7, default='#fbf3e6', help_text="Weekend shading in search")
    theme_atypical = models.CharField(max_length=7, default='#eef1f4', help_text="Outside-typical-hours shading")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.club.name} Config"


class Role(models.Model):
    """Extensible system roles per club."""
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='roles')
    name = models.CharField(max_length=50)  # 'instructor', 'admin', 'member'
    
    class Meta:
        unique_together = ('club', 'name')
    
    def __str__(self):
        return f"{self.club.name} - {self.name}"


class MembershipCategory(models.Model):
    """Extensible membership categories per club."""
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='membership_categories')
    name = models.CharField(max_length=100)  # 'Private Pilot', 'Student', etc.
    is_member = models.BooleanField(default=True, help_text="True=member, False=non-member")
    
    class Meta:
        unique_together = ('club', 'name')
        verbose_name_plural = "Membership categories"
    
    def __str__(self):
        status = "Member" if self.is_member else "Non-Member"
        return f"{self.club.name} - {self.name} ({status})"


class ClubMember(models.Model):
    """
    A person's membership at a club. Holds personal details, standing, and subscription info.
    A User can have memberships at multiple clubs (one ClubMember per club).
    """
    STANDING_CHOICES = [
        ('pending',    'Pending Approval'),
        ('active',     'Active'),
        ('suspended',  'Suspended'),
        ('lapsed',     'Lapsed'),
        ('resigned',   'Resigned'),
        ('non_member', 'Non-member'),   # Young Eagles, trial flights, etc.
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='club_memberships', null=True, blank=True)
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='members')

    # ── Personal details ──────────────────────────────────────────────────────
    caa_number    = models.CharField(max_length=10, blank=True)
    phone_mobile  = models.CharField(max_length=20, blank=True)
    phone_home    = models.CharField(max_length=20, blank=True)
    phone_work    = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    suburb        = models.CharField(max_length=100, blank=True)
    postcode      = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)

    # ── Membership standing & subscription ───────────────────────────────────
    standing = models.CharField(max_length=20, choices=STANDING_CHOICES, default='active')
    membership_category = models.ForeignKey(MembershipCategory, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)

    avatar               = models.ImageField(upload_to='avatars/', null=True, blank=True)
    join_date            = models.DateField(auto_now_add=True, help_text="Date first admitted")
    subscription_expires = models.DateField(null=True, blank=True, help_text="Current subscription valid until")
    last_renewed         = models.DateField(null=True, blank=True, help_text="Date of most recent subscription payment")

    class Meta:
        unique_together = ('user', 'club')
        ordering = ['user__last_name']

    def __str__(self):
        role_str = self.role.name if self.role else 'No role'
        return f"{self.user.get_full_name() if self.user else '—'} @ {self.club.name} ({role_str})"

    @property
    def is_current(self):
        """True when the member is active AND their subscription has not expired."""
        if self.standing != 'active':
            return False
        if self.subscription_expires and self.subscription_expires < date.today():
            return False
        return True

    @property
    def is_member(self):
        return self.standing != 'non_member'

    @property
    def is_instructor(self):
        return self.role and self.role.name.lower() == 'instructor'

    @property
    def is_admin(self):
        return self.role and self.role.name.lower() == 'admin'

    @property
    def is_staff(self):
        return self.is_instructor or self.is_admin or (self.role and self.role.name.lower() == 'staff')


# ============================================================================
# REGULATORY TRACKING
# ============================================================================

class CredentialType(models.TextChoices):
    """License/certificate types."""
    PPL = 'ppl', 'Private Pilot Licence'
    COMMERCIAL = 'commercial', 'Commercial Licence'
    ATPL = 'atpl', 'Airline Transport Pilot'
    INSTRUCTOR = 'instructor', 'Instructor Rating'
    BFR = 'bfr', 'Biennial Flight Review'
    MEDICAL_CLASS1 = 'medical_c1', 'Class 1 Medical'
    MEDICAL_CLASS2 = 'medical_c2', 'Class 2 Medical'


class MemberCredential(models.Model):
    """
    Tracks pilot licenses, medical certificates, BFR, etc.
    One record per credential per member.
    """
    club_member = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='credentials')
    credential_type = models.CharField(max_length=20, choices=CredentialType.choices)
    
    # Validity
    issue_date = models.DateField()
    expiry_date = models.DateField()
    
    # Document tracking
    certificate_number = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('club_member', 'credential_type')
        ordering = ['-expiry_date']
    
    def __str__(self):
        return f"{self.club_member.user.last_name} - {self.get_credential_type_display()}"
    
    @property
    def days_until_expiry(self):
        delta = self.expiry_date - date.today()
        return delta.days
    
    @property
    def is_expired(self):
        return self.expiry_date < date.today()
    
    @property
    def is_expiring_soon(self):
        """Warning threshold: 30 days."""
        return 0 <= self.days_until_expiry <= 30


# ============================================================================
# AIRCRAFT & MAINTENANCE
# ============================================================================

class AircraftStatus(models.TextChoices):
    """Aircraft operational status. Temporary unavailability (maintenance, grounding) is
    managed via block-outs rather than status changes."""
    ONLINE = 'online', 'Online'
    RETIRED = 'retired', 'Retired'


class Aircraft(models.Model):
    """
    Aircraft in the fleet. Tracks hobbs/tacho, maintenance schedule, availability.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='aircraft')
    
    # Identification
    registration = models.CharField(max_length=10)
    aircraft_type = models.CharField(max_length=50)
    serial_number = models.CharField(max_length=50, blank=True)
    
    # Configuration
    engine_count = models.IntegerField(default=1)
    seats = models.IntegerField(default=2)
    
    # Recording instruments
    records_hobbs = models.BooleanField(default=True)
    records_tacho = models.BooleanField(default=False)
    records_airswitch = models.BooleanField(default=False)
    
    # Time calculation
    TOTAL_TIME_METHOD_CHOICES = [
        ('hobbs', 'Hobbs Meter'),
        ('tacho', 'Tachometer'),
        ('tacho_less_5', 'Tacho - 5%'),
    ]
    total_time_method = models.CharField(max_length=20, choices=TOTAL_TIME_METHOD_CHOICES, default='hobbs')
    
    # Fuel
    fuel_consumption_per_hour = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    # Status
    status = models.CharField(max_length=20, choices=AircraftStatus.choices, default=AircraftStatus.ONLINE)
    is_available_for_hire = models.BooleanField(default=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('club', 'registration')
        ordering = ['registration']
    
    def __str__(self):
        return f"{self.registration} ({self.aircraft_type})"
    
    @property
    def is_online(self):
        return self.status == AircraftStatus.ONLINE and self.is_available_for_hire


class MaintenanceUrgency(models.TextChoices):
    """Visual indicator for maintenance urgency."""
    GREEN = 'green', 'Within limits'
    AMBER = 'amber', 'Due soon'
    RED = 'red', 'Overdue/grounded'


class AircraftMaintenanceItem(models.Model):
    """
    Recurring maintenance tasks: 100-hour check, oil change, etc.
    Tracks both calendar and flight-hour based intervals.
    """
    aircraft = models.ForeignKey(Aircraft, on_delete=models.CASCADE, related_name='maintenance_items')
    
    # What needs doing
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # Scheduling: calendar-based
    due_date = models.DateField(null=True, blank=True)
    
    # Scheduling: flight-hour based
    due_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    
    # When it was last done
    last_completed_date = models.DateField(null=True, blank=True)
    last_completed_hours = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    
    # Urgency (calculated, for UI display)
    urgency = models.CharField(max_length=10, choices=MaintenanceUrgency.choices, default=MaintenanceUrgency.GREEN)
    
    class Meta:
        ordering = ['due_date', 'due_hours']
    
    def __str__(self):
        return f"{self.aircraft.registration} - {self.name}"
    
    @property
    def days_until_due(self):
        if self.due_date:
            delta = self.due_date - date.today()
            return delta.days
        return None


# ============================================================================
# FLIGHT TYPES & BILLING
# ============================================================================

class FlightType(models.Model):
    """
    Flight categories for charging & tracking.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='flight_types')

    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)

    # Flags
    is_billable = models.BooleanField(default=True)
    is_training = models.BooleanField(default=False)
    is_solo = models.BooleanField(default=False, help_text="Solo flights — instructor is not required")

    class Meta:
        unique_together = ('club', 'code')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.club.name})"


class ChargeRate(models.Model):
    """
    Billing rates by aircraft, flight type, and time-recording method.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='charge_rates')
    aircraft = models.ForeignKey(Aircraft, on_delete=models.CASCADE, related_name='charge_rates')
    flight_type = models.ForeignKey(FlightType, on_delete=models.CASCADE, related_name='charge_rates')
    
    # Time recording basis
    TIME_METHOD_CHOICES = [
        ('hobbs', 'Hobbs Hour'),
        ('tacho', 'Tachometer Hour'),
    ]
    time_method = models.CharField(max_length=20, choices=TIME_METHOD_CHOICES)
    
    # Rate
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3, default='NZD')
    
    # Modifiers
    includes_fuel = models.BooleanField(default=False)
    
    class Meta:
        unique_together = ('aircraft', 'flight_type', 'time_method')
    
    def __str__(self):
        return f"{self.aircraft.registration} - {self.flight_type.name}: ${self.amount}"


# ============================================================================
# ACCOUNTS & BILLING
# ============================================================================

class Account(models.Model):
    """
    Member's financial account. Tracks balance and credit.
    credit_limit=None means exempt from warnings (typically instructors).
    """
    club_member = models.OneToOneField(ClubMember, on_delete=models.CASCADE, related_name='account')

    balance      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_limit = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Max negative balance allowed. Null = exempt (e.g. instructors)."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Accounts'

    def __str__(self):
        name = self.club_member.user.get_full_name() if self.club_member.user else '—'
        return f"Account: {name} ({self.balance})"

    @property
    def has_warning(self):
        """True when balance is negative beyond the allowed credit limit."""
        if self.credit_limit is None:
            return False   # exempt
        return self.balance < -self.credit_limit


# ============================================================================
# BOOKINGS
# ============================================================================

class BookingStatus(models.TextChoices):
    """Booking lifecycle states."""
    PENDING = 'pending', 'Pending Confirmation'
    CONFIRMED = 'confirmed', 'Confirmed'
    COMPLETED = 'completed', 'Completed'
    CANCELLED = 'cancelled', 'Cancelled'


class Booking(models.Model):
    """
    Flight booking. Member requests a time slot; instructor confirms.
    Billing happens at completion.
    """
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='bookings')
    
    # Participants
    member = models.ForeignKey(ClubMember, on_delete=models.CASCADE, related_name='bookings')
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='bookings_created')
    confirmed_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='bookings_confirmed'
    )
    
    # Flight details
    aircraft = models.ForeignKey(Aircraft, on_delete=models.PROTECT, related_name='bookings')
    flight_type = models.ForeignKey(FlightType, on_delete=models.PROTECT)
    instructor = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='flights_instructed'
    )
    
    # Timing
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    
    # Status & notes
    status = models.CharField(max_length=20, choices=BookingStatus.choices, default=BookingStatus.PENDING)
    description = models.TextField(blank=True)
    
    # Flags
    is_maintenance_booking = models.BooleanField(default=False)

    # Block-out interaction
    blockout_conflict = models.BooleanField(
        default=False,
        help_text="Set when a block-out was added over this existing booking. Staff must resolve."
    )
    blockout_conflict_reason = models.CharField(max_length=255, blank=True)
    blockout_override = models.BooleanField(
        default=False,
        help_text="Set when staff deliberately booked over a block-out."
    )
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-scheduled_start']
        indexes = [
            models.Index(fields=['club', 'scheduled_start']),
            models.Index(fields=['member', 'status']),
        ]
    
    def __str__(self):
        return f"{self.member.user.last_name} - {self.aircraft.registration} ({self.scheduled_start.date()})"
    
    def save(self, *args, **kwargs):
        if self.confirmed_by:
            confirmer = ClubMember.objects.filter(user=self.confirmed_by, club=self.club).first()
            if not (confirmer and confirmer.is_staff):
                raise ValueError("Only staff can confirm bookings.")
        super().save(*args, **kwargs)


class BookingAuditLog(models.Model):
    """
    Immutable event log. Records every state change on a booking.
    Used for compliance & audit trail.
    """
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='audit_logs')
    
    # Event
    EVENT_CHOICES = [
        ('created', 'Created'),
        ('confirmed', 'Confirmed'),
        ('field_changed', 'Field Changed'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('warning_acknowledged', 'Warning Acknowledged'),
    ]
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES)
    
    # Who did it
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    
    # What changed (optional)
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    
    # Context (optional)
    notes = models.TextField(blank=True)
    
    # Timestamp (immutable)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
        verbose_name_plural = 'Booking Audit Logs'
    
    def __str__(self):
        return f"{self.booking} - {self.get_event_type_display()} by {self.user.last_name}"


class FlightCompletion(models.Model):
    """
    Logged flight data: actual hours, charges, payment.
    Created after booking is completed.
    Triggers billing.
    """
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='flight_completion')
    
    # Actual flight time
    hobbs_start = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    hobbs_end = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_start = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    tacho_end = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    
    # Calculated
    actual_flight_hours = models.DecimalField(max_digits=6, decimal_places=2)
    
    # Charges
    base_charge = models.DecimalField(max_digits=8, decimal_places=2)
    fuel_surcharge = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    landing_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    other_charges = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total_charge = models.DecimalField(max_digits=8, decimal_places=2)
    
    # Payment
    PAYMENT_METHOD_CHOICES = [
        ('credit', 'Account Credit'),
        ('eftpos', 'EFTPOS'),
        ('invoice', 'Invoice (Bank Transfer)'),
    ]
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    
    # Audit
    logged_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.booking} - {self.actual_flight_hours}h"


class MemberCategory(models.Model):
    """Extensible member categories per club."""
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='member_categories')
    name = models.CharField(max_length=100)  # 'Private Pilot', 'Student', 'Life Member (Flying)', etc.
    slug = models.SlugField()
    status = models.CharField(max_length=20, choices=[('member', 'Member'), ('non_member', 'Non-Member')])
    description = models.TextField(blank=True)
    
    class Meta:
        unique_together = ('club', 'slug')
        ordering = ['status', 'name']
    
    def __str__(self):
        return self.name


class BlockOutType(models.Model):
    """Extensible block-out categories per club (Maintenance, Lunch, Admin, etc.)."""
    TARGET_AIRCRAFT = 'aircraft'
    TARGET_INSTRUCTOR = 'instructor'
    TARGET_ALL = 'all'
    TARGET_CHOICES = [
        ('aircraft',   'Aircraft'),
        ('instructor', 'Instructor'),
        ('all',        'All resources'),
    ]

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='blockout_types')
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default='#9aa3ad', help_text="Hex colour for the calendar")

    target = models.CharField(
        max_length=20, choices=TARGET_CHOICES, default='all',
        help_text="Which resource type this block-out applies to."
    )
    is_hard = models.BooleanField(
        default=True,
        help_text="Hard: blocks bookings (staff can override). Soft: advisory — anyone can confirm and proceed. "
                  "Aircraft block-out types are always hard regardless of this flag."
    )

    class Meta:
        ordering = ['target', 'name']

    def __str__(self):
        return self.name

    @property
    def effective_is_hard(self):
        """Aircraft blocks are always hard; instructor blocks respect is_hard."""
        return self.target == self.TARGET_AIRCRAFT or self.is_hard


class BlockOut(models.Model):
    """
    A period during which a resource (or all resources) is unavailable for booking.
    Set by instructors/admins. Subtracted from availability search.
    """
    RECUR_CHOICES = [
        ('one_off', 'One-off'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
    ]
    SCOPE_CHOICES = [
        ('all', 'All resources'),
        ('aircraft', 'Specific aircraft'),
        ('instructors', 'Specific instructors'),
    ]

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='blockouts')
    blockout_type = models.ForeignKey(BlockOutType, on_delete=models.SET_NULL, null=True, blank=True)
    label = models.CharField(max_length=150, blank=True, help_text="Optional note, e.g. '100hr check'")

    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='all')
    aircraft = models.ManyToManyField('Aircraft', blank=True, related_name='blockouts')
    instructors = models.ManyToManyField('User', blank=True, related_name='blockouts')

    recurrence = models.CharField(max_length=10, choices=RECUR_CHOICES, default='one_off')
    # one_off: uses date. weekly: uses weekday (0=Mon..6=Sun). daily: every day.
    date = models.DateField(null=True, blank=True, help_text="For one-off blocks")
    weekday = models.IntegerField(null=True, blank=True, help_text="0=Mon..6=Sun, for weekly")

    all_day = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    # Optional bounding window for recurring blocks
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_blockouts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['recurrence', 'start_time']

    def __str__(self):
        t = self.blockout_type.name if self.blockout_type else 'Block'
        return f"{t} ({self.get_recurrence_display()})"

    def applies_on(self, day):
        """Does this block-out occur on the given date?"""
        if self.active_from and day < self.active_from:
            return False
        if self.active_until and day > self.active_until:
            return False
        if self.recurrence == 'one_off':
            return self.date == day
        if self.recurrence == 'daily':
            return True
        if self.recurrence == 'weekly':
            return self.weekday == day.weekday()
        return False

    def affects_aircraft(self, aircraft):
        if self.scope == 'all':
            return True
        if self.scope == 'aircraft':
            return self.aircraft.filter(id=aircraft.id).exists()
        return False  # instructor-scoped blocks don't block aircraft

    def affects_instructor(self, user):
        if self.scope == 'all':
            return True
        if self.scope == 'instructors':
            return self.instructors.filter(id=user.id).exists()
        return False

    def affects_booking(self, booking):
        """Does this block-out cover the resource(s) this booking uses?"""
        if self.scope == 'all':
            return True
        if self.scope == 'aircraft':
            return booking.aircraft_id and self.aircraft.filter(id=booking.aircraft_id).exists()
        if self.scope == 'instructors':
            return booking.instructor_id and self.instructors.filter(id=booking.instructor_id).exists()
        return False

    def interval_on(self, day):
        """Return (start_dt, end_dt) this block occupies on a given day, or None."""
        from django.utils import timezone as _tz
        if not self.applies_on(day):
            return None
        if self.all_day or not (self.start_time and self.end_time):
            s = datetime.combine(day, time(0, 0))
            e = datetime.combine(day, time(23, 59))
        else:
            s = datetime.combine(day, self.start_time)
            e = datetime.combine(day, self.end_time)
        if _tz.is_naive(s):
            s = _tz.make_aware(s)
        if _tz.is_naive(e):
            e = _tz.make_aware(e)
        return (s, e)

    def overlaps_booking(self, booking):
        """True if this block-out, on the booking's local day(s), overlaps it in time and scope."""
        from django.utils import timezone as _tz
        if not self.affects_booking(booking):
            return False
        local_start = _tz.localtime(booking.scheduled_start)
        local_end = _tz.localtime(booking.scheduled_end)
        for day in {local_start.date(), local_end.date()}:
            iv = self.interval_on(day)
            if iv and iv[0] < booking.scheduled_end and iv[1] > booking.scheduled_start:
                return True
        return False

    def rescan_bookings(self):
        """
        Re-evaluate conflict flags for bookings potentially affected by this block-out.
        Scans from midnight today so bookings earlier today are not missed when a
        block-out is added after those bookings have already ended.
        """
        from django.utils import timezone as _tz
        from datetime import datetime, time as _time
        today_start = _tz.make_aware(datetime.combine(_tz.localdate(), _time.min))
        affected = Booking.objects.filter(
            club=self.club,
            scheduled_end__gte=today_start,
        ).exclude(status='cancelled')
        for b in affected:
            recompute_blockout_conflict(b)


def blockout_conflicts_for_booking(booking):
    """
    Return the list of BlockOut objects that conflict with this booking
    (overlap in time AND cover its aircraft/instructor by scope).
    """
    from django.utils import timezone as _tz
    # Use the booking's LOCAL day(s); a UTC-stored booking can span the local
    # date boundary, so check both the local start and end dates.
    local_start = _tz.localtime(booking.scheduled_start)
    local_end = _tz.localtime(booking.scheduled_end)
    days = {local_start.date(), local_end.date()}
    hits = []
    for bo in BlockOut.objects.filter(club=booking.club).prefetch_related('aircraft', 'instructors', 'blockout_type'):
        if not bo.affects_booking(booking):
            continue
        for day in days:
            iv = bo.interval_on(day)
            if iv and iv[0] < booking.scheduled_end and iv[1] > booking.scheduled_start:
                hits.append(bo)
                break
    return hits


def recompute_blockout_conflict(booking, save=True):
    """
    Recompute the blockout_conflict flag for a booking from current block-outs.
    A staff override (blockout_override=True) suppresses the conflict flag.
    Returns True if the booking is currently in conflict (pre-override).
    """
    hits = blockout_conflicts_for_booking(booking)
    in_conflict = bool(hits)

    if booking.blockout_override:
        # staff already chose to book over a block; keep clear but remember reason
        new_flag = False
        reason = ''
    else:
        new_flag = in_conflict
        reason = ''
        if hits:
            first = hits[0]
            tname = first.blockout_type.name if first.blockout_type else (first.label or 'block-out')
            reason = f"Overlaps {tname}"

    if save and (booking.blockout_conflict != new_flag or booking.blockout_conflict_reason != reason):
        booking.blockout_conflict = new_flag
        booking.blockout_conflict_reason = reason
        booking.save(update_fields=['blockout_conflict', 'blockout_conflict_reason'])
    else:
        booking.blockout_conflict = new_flag
        booking.blockout_conflict_reason = reason
    return in_conflict


class InstructorAvailability(models.Model):
    """
    Declares when an instructor is on-roster and available to be booked.
    The availability search intersects aircraft free time with this schedule.
    An instructor with no records here is assumed available during all operating hours.
    """
    RECURRENCE_CHOICES = [
        ('weekly', 'Weekly (recurring)'),
        ('one_off', 'Specific date'),
    ]
    WEEKDAY_CHOICES = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'),
        (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]

    club_member = models.ForeignKey(
        ClubMember, on_delete=models.CASCADE, related_name='availability_windows'
    )
    recurrence = models.CharField(max_length=10, choices=RECURRENCE_CHOICES, default='weekly')

    # For weekly recurrence
    weekday = models.IntegerField(null=True, blank=True, choices=WEEKDAY_CHOICES,
                                  help_text='0=Mon … 6=Sun')
    # For one-off
    date = models.DateField(null=True, blank=True)

    all_day = models.BooleanField(default=True, help_text='Available the full operating day')
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    # Optional bounding window for recurring entries
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)

    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['recurrence', 'weekday', 'start_time']

    def __str__(self):
        name = self.club_member.user.get_full_name() if self.club_member.user else '?'
        if self.recurrence == 'weekly':
            day = dict(self.WEEKDAY_CHOICES).get(self.weekday, '?')
            hours = 'all day' if self.all_day else f"{self.start_time}–{self.end_time}"
            return f"{name} — {day} {hours}"
        return f"{name} — {self.date}"

    def applies_on(self, day):
        if self.active_from and day < self.active_from:
            return False
        if self.active_until and day > self.active_until:
            return False
        if self.recurrence == 'one_off':
            return self.date == day
        if self.recurrence == 'weekly':
            return self.weekday == day.weekday()
        return False

    def interval_on(self, day, day_start, day_end):
        """Return (start_dt, end_dt) this availability window covers on a given day."""
        from django.utils import timezone as _tz
        if not self.applies_on(day):
            return None
        if self.all_day or not (self.start_time and self.end_time):
            return (day_start, day_end)
        s = _tz.make_aware(datetime.combine(day, self.start_time))
        e = _tz.make_aware(datetime.combine(day, self.end_time))
        return (max(s, day_start), min(e, day_end))


# --- Block-out signals: keep booking conflict flags in sync -------------
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver


@receiver(post_save, sender=BlockOut)
def _blockout_saved(sender, instance, **kwargs):
    # Defer until M2M is set if scope needs it; rescan is cheap & idempotent.
    instance.rescan_bookings()


@receiver(post_delete, sender=BlockOut)
def _blockout_deleted(sender, instance, **kwargs):
    # On delete, re-evaluate all upcoming bookings so stale flags clear.
    from django.utils import timezone as _tz
    for b in Booking.objects.filter(club=instance.club, scheduled_end__gte=_tz.now()).exclude(status='cancelled'):
        recompute_blockout_conflict(b)


@receiver(m2m_changed, sender=BlockOut.aircraft.through)
def _blockout_aircraft_changed(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        instance.rescan_bookings()


@receiver(m2m_changed, sender=BlockOut.instructors.through)
def _blockout_instructors_changed(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        instance.rescan_bookings()
