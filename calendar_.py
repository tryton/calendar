#This file is part of Tryton.  The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.
import uuid
import vobject
import dateutil.tz
import pytz
import datetime
import xml.dom.minidom
from trytond.model import ModelSQL, ModelView, fields
from trytond.tools import reduce_ids
from trytond.backend import TableHandler
from trytond.pyson import If, Bool, Eval
from trytond.transaction import Transaction
from trytond.cache import Cache
from trytond.pool import Pool

__all__ = ['Calendar', 'ReadUser', 'WriteUser', 'Category', 'Location',
    'Event', 'EventCategory', 'Alarm', 'EventAlarm', 'Attendee',
    'EventAttendee', 'Date', 'EventRDate', 'EventExDate', 'RRule',
    'EventRRule', 'EventExRule']

tzlocal = dateutil.tz.tzlocal()
tzutc = dateutil.tz.tzutc()
domimpl = xml.dom.minidom.getDOMImplementation()


class Calendar(ModelSQL, ModelView):
    "Calendar"
    __name__ = 'calendar.calendar'
    name = fields.Char('Name', required=True, select=True)
    description = fields.Text('Description')
    owner = fields.Many2One('res.user', 'Owner', select=True,
            domain=[('email', '!=', None)],
            help='The user must have an email')
    read_users = fields.Many2Many('calendar.calendar-read-res.user',
            'calendar', 'user', 'Read Users')
    write_users = fields.Many2Many('calendar.calendar-write-res.user',
            'calendar', 'user', 'Write Users')
    _get_name_cache = Cache('calendar_calendar.get_name')

    @classmethod
    def __setup__(cls):
        super(Calendar, cls).__setup__()
        cls._sql_constraints = [
            ('name_uniq', 'UNIQUE(name)',
                'The name of calendar must be unique!'),
            ('owner_uniq', 'UNIQUE(owner)',
                'A user can have only one calendar!'),
            ]
        cls._order.insert(0, ('name', 'ASC'))
        cls._constraints += [
            ('check_name', 'Calendar name can not end with .ics'),
            ]

    @classmethod
    def create(cls, vals):
        calendar = super(Calendar, cls).create(vals)
        # Restart the cache for get_name
        cls._get_name_cache.clear()
        return calendar

    @classmethod
    def write(cls, calendars, vals):
        super(Calendar, cls).write(calendars, vals)
        # Restart the cache for get_name
        cls._get_name_cache.clear()

    @classmethod
    def delete(cls, calendars):
        super(Calendar, cls).delete(calendars)
        # Restart the cache for calendar
        cls._get_name_cache.clear()

    def check_name(self):
        '''
        Check the name doesn't end with .ics
        '''
        if self.name.endswith('.ics'):
            return False
        return True

    @classmethod
    def get_name(cls, name):
        '''
        Return the calendar id of the name
        '''
        calendar_id = cls._get_name_cache.get(name, default=-1)
        if calendar_id == -1:
            calendars = cls.search([
                ('name', '=', name),
                ], limit=1)
            if calendars:
                calendar_id = calendars[0].id
            else:
                calendar_id = None
            cls._get_name_cache.set(name, calendar_id)
        return calendar_id

    @staticmethod
    def calendar2ical(calendar_id):
        '''
        Return an iCalendar object for the given calendar_id containing
        all the vevent objects
        '''
        Event = Pool().get('calendar.event')

        ical = vobject.iCalendar()
        ical.vevent_list = []
        events = Event.search([
                ('calendar', '=', calendar_id),
                ('parent', '=', None),
                ])
        for event in events:
            ical2 = event.event2ical()
            ical.vevent_list.extend(ical2.vevent_list)
        return ical

    @property
    def _fbtype(self):
        '''
        Return the freebusy type for give transparent and status
        '''
        if self.transp == 'opaque':
            if not self.status or self.status == 'confirmed':
                fbtype = 'BUSY'
            elif self.status == 'cancelled':
                fbtype = 'FREE'
            elif self.status == 'tentative':
                fbtype = 'BUSY-TENTATIVE'
            else:
                fbtype = 'BUSY'
        else:
            fbtype = 'FREE'
        return fbtype

    @classmethod
    def freebusy(cls, calendar_id, dtstart, dtend):
        '''
        Return an iCalendar object for the given calendar_id with the
        vfreebusy objects between the two dates
        '''
        Event = Pool().get('calendar.event')

        ical = vobject.iCalendar()
        ical.add('method').value = 'REPLY'
        ical.add('vfreebusy')
        if not isinstance(dtstart, datetime.datetime):
            ical.vfreebusy.add('dtstart').value = dtstart
            dtstart = datetime.datetime.combine(dtstart, datetime.time())\
                    .replace(tzinfo=tzlocal)
        else:
            ical.vfreebusy.add('dtstart').value = dtstart.astimezone(tzutc)
        if not isinstance(dtend, datetime.datetime):
            ical.vfreebusy.add('dtend').value = dtend
            dtend = datetime.datetime.combine(dtend, datetime.time.max)\
                    .replace(tzinfo=tzlocal)
        else:
            ical.vfreebusy.add('dtend').value = dtend.astimezone(tzutc)

        with Transaction().set_user(0):
            events = Event.search([
                    ['OR',
                        [('dtstart', '<=', dtstart),
                            ('dtend', '>=', dtstart)],
                        [('dtstart', '<=', dtend),
                            ('dtend', '>=', dtend)],
                        [('dtstart', '>=', dtstart),
                            ('dtend', '<=', dtend)],
                        [('dtstart', '>=', dtstart),
                            ('dtstart', '<=', dtend),
                            ('dtend', '=', None)]],
                    ('parent', '=', None),
                    ('rdates', '=', None),
                    ('rrules', '=', None),
                    ('exdates', '=', None),
                    ('exrules', '=', None),
                    ('occurences', '=', None),
                    ('calendar', '=', calendar_id),
                    ])

        for event in events:
            # Don't group freebusy as sunbird doesn't handle it
            freebusy = ical.vfreebusy.add('freebusy')
            freebusy.fbtype_param = event._fbtype
            if event.dtstart.replace(tzinfo=tzlocal) >= dtstart:
                freebusy_dtstart = event.dtstart.replace(tzinfo=tzlocal)
            else:
                freebusy_dtstart = dtstart
            if event.dtend.replace(tzinfo=tzlocal) <= dtend:
                freebusy_dtend = event.dtend.replace(tzinfo=tzlocal)
            else:
                freebusy_dtend = dtend
            freebusy.value = [(
                freebusy_dtstart.astimezone(tzutc),
                freebusy_dtend.astimezone(tzutc))]

        with Transaction().set_user(0):
            events = Event.search([
                    ('parent', '=', None),
                    ('dtstart', '<=', dtend),
                    ['OR',
                        ('rdates', '!=', None),
                        ('rrules', '!=', None),
                        ('exdates', '!=', None),
                        ('exrules', '!=', None),
                        ('occurences', '!=', None),
                        ],
                    ('calendar', '=', calendar_id),
                    ])

        for event in events:
            event_ical = event.event2ical()
            if event_ical.vevent.rruleset:
                between_dtstart, between_dtend = dtstart, dtend
                for freebusy_dtstart in event_ical.vevent.rruleset:
                    if freebusy_dtstart.replace(tzinfo=tzlocal) > dtend:
                        break
                    if not event.dtend:
                        freebusy_dtend = freebusy_dtstart
                    else:
                        freebusy_dtend = event.dtend.replace(tzinfo=tzlocal)\
                                - event.dtstart.replace(tzinfo=tzlocal) \
                                + freebusy_dtstart
                    f_dtstart_tz = freebusy_dtstart.replace(tzinfo=tzlocal)
                    f_dtend_tz = freebusy_dtend.replace(tzinfo=tzlocal)
                    if not ((f_dtstart_tz <= dtstart
                                and f_dtend_tz >= dtstart)
                            or (f_dtstart_tz <= dtend
                                and f_dtend_tz >= dtend)
                            or (f_dtstart_tz >= dtstart
                                and f_dtend_tz <= dtend)):
                        continue
                    freebusy_fbtype = event._fbtype
                    all_day = event.all_day
                    for occurence in event.occurences:
                        if (occurence.recurrence.replace(tzinfo=tzlocal)
                                == f_dtstart_tz):
                            freebusy_dtstart = \
                                occurence.dtstart.replace(tzinfo=tzlocal)
                            if occurence.dtend:
                                freebusy_dtend = occurence.dtend\
                                        .replace(tzinfo=tzlocal)
                            else:
                                freebusy_dtend = freebusy_dtstart
                            all_day = occurence.all_day
                            freebusy_fbtype = occurence._fbtype
                            break
                    freebusy = ical.vfreebusy.add('freebusy')
                    freebusy.fbtype_param = freebusy_fbtype
                    if f_dtstart_tz <= dtstart:
                        freebusy_dtstart = dtstart
                    if f_dtend_tz >= dtend:
                        freebusy_dtend = dtend
                    if all_day:
                        freebusy.value = [(
                                f_dtstart_tz.astimezone(tzutc),
                                f_dtend_tz.astimezone(tzutc),
                                )]
                    else:
                        freebusy.value = [(
                            freebusy_dtstart.astimezone(tzutc),
                            freebusy_dtend.astimezone(tzutc))]
        return ical

    @classmethod
    def post(cls, uri, data):
        '''
        Handle post of vfreebusy request and return the XML with
        schedule-response
        '''
        from pywebdav.lib.errors import DAV_Forbidden
        Collection = Pool().get('webdav.collection')

        calendar_id = Collection.calendar(uri)
        if not calendar_id:
            raise DAV_Forbidden
        calendar = cls(calendar_id)
        if calendar.owner.id != Transaction().user:
            raise DAV_Forbidden
        ical = vobject.readOne(data)
        if ical.method.value == 'REQUEST' \
                and hasattr(ical, 'vfreebusy'):
            doc = domimpl.createDocument(None, 'schedule-response', None)
            sr = doc.documentElement
            sr.setAttribute('xmlns:D', 'DAV:')
            sr.setAttribute('xmlns:C', 'urn:ietf:params:xml:ns:caldav')
            sr.tagName = 'C:schedule-response'

            if not isinstance(ical.vfreebusy.dtstart.value, datetime.datetime):
                dtstart = ical.vfreebusy.dtstart.value
            else:
                if ical.vfreebusy.dtstart.value.tzinfo:
                    dtstart = ical.vfreebusy.dtstart.value.astimezone(tzlocal)
                else:
                    dtstart = ical.vfreebusy.dtstart.value
            if not isinstance(ical.vfreebusy.dtend.value, datetime.datetime):
                dtend = ical.vfreebusy.dtend.value
            else:
                if ical.vfreebusy.dtend.value.tzinfo:
                    dtend = ical.vfreebusy.dtend.value.astimezone(tzlocal)
                else:
                    dtend = ical.vfreebusy.dtend.value
            for attendee in ical.vfreebusy.attendee_list:
                resp = doc.createElement('C:response')
                sr.appendChild(resp)
                recipient = doc.createElement('C:recipient')
                href = doc.createElement('D:href')
                huri = doc.createTextNode(attendee.value)
                href.appendChild(huri)
                recipient.appendChild(href)
                resp.appendChild(recipient)

                vfreebusy = None
                email = attendee.value
                if attendee.value.lower().startswith('mailto:'):
                    email = attendee.value[7:]
                with Transaction().set_user(0):
                    calendars = cls.search([
                            ('owner.email', '=', email),
                            ])
                if calendars:
                    vfreebusy = cls.freebusy(calendars[0].id, dtstart, dtend)
                    vfreebusy.vfreebusy.add('dtstamp').value = \
                            ical.vfreebusy.dtstamp.value
                    vfreebusy.vfreebusy.add('uid').value = \
                            ical.vfreebusy.uid.value
                    vfreebusy.vfreebusy.add('organizer').value = \
                            ical.vfreebusy.organizer.value
                    vfreebusy.vfreebusy.add('attendee').value = attendee.value

                status = doc.createElement('C:request-status')
                status.appendChild(doc.createTextNode(vfreebusy
                        and '2.0;Success'
                        or '5.3;No scheduling support for user.'))
                resp.appendChild(status)
                if vfreebusy:
                    data = doc.createElement('C:calendar-data')
                    data.appendChild(doc.createTextNode(vfreebusy.serialize()))
                    resp.appendChild(data)
            return doc.toxml(encoding='utf-8')
        raise DAV_Forbidden


class ReadUser(ModelSQL):
    'Calendar - read - User'
    __name__ = 'calendar.calendar-read-res.user'
    calendar = fields.Many2One('calendar.calendar', 'Calendar',
            ondelete='CASCADE', required=True, select=True)
    user = fields.Many2One('res.user', 'User', ondelete='CASCADE',
            required=True, select=True)


class WriteUser(ModelSQL):
    'Calendar - write - User'
    __name__ = 'calendar.calendar-write-res.user'
    calendar = fields.Many2One('calendar.calendar', 'Calendar',
            ondelete='CASCADE', required=True, select=True)
    user = fields.Many2One('res.user', 'User', ondelete='CASCADE',
            required=True, select=True)


class Category(ModelSQL, ModelView):
    "Category"
    __name__ = 'calendar.category'
    name = fields.Char('Name', required=True, select=True)

    @classmethod
    def __setup__(cls):
        super(Category, cls).__setup__()
        cls._sql_constraints = [
            ('name_uniq', 'UNIQUE(name)',
                'The name of calendar category must be unique!'),
            ]
        cls._order.insert(0, ('name', 'ASC'))


class Location(ModelSQL, ModelView):
    "Location"
    __name__ = 'calendar.location'
    name = fields.Char('Name', required=True, select=True)

    @classmethod
    def __setup__(cls):
        super(Location, cls).__setup__()
        cls._sql_constraints = [
            ('name_uniq', 'UNIQUE(name)',
                'The name of calendar location must be unique!'),
            ]
        cls._order.insert(0, ('name', 'ASC'))


class Event(ModelSQL, ModelView):
    "Event"
    __name__ = 'calendar.event'
    _rec_name = 'uuid'
    uuid = fields.Char('UUID', required=True,
            help='Universally Unique Identifier', select=True)
    calendar = fields.Many2One('calendar.calendar', 'Calendar',
            required=True, select=True, ondelete="CASCADE")
    summary = fields.Char('Summary')
    sequence = fields.Integer('Sequence', required=True)
    description = fields.Text('Description')
    all_day = fields.Boolean('All Day')
    dtstart = fields.DateTime('Start Date', required=True, select=True)
    dtend = fields.DateTime('End Date', select=True)
    timezone = fields.Selection('timezones', 'Timezone')
    categories = fields.Many2Many('calendar.event-calendar.category',
            'event', 'category', 'Categories')
    classification = fields.Selection([
        ('public', 'Public'),
        ('private', 'Private'),
        ('confidential', 'Confidential'),
        ], 'Classification', required=True)
    location = fields.Many2One('calendar.location', 'Location')
    status = fields.Selection([
        ('', ''),
        ('tentative', 'Tentative'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ], 'Status')
    organizer = fields.Char('Organizer', states={
            'required': If(Bool(Eval('attendees')), ~Eval('parent'), False),
            }, depends=['attendees', 'parent'])
    attendees = fields.One2Many('calendar.event.attendee', 'event',
            'Attendees')
    transp = fields.Selection([
        ('opaque', 'Opaque'),
        ('transparent', 'Transparent'),
        ], 'Time Transparency', required=True)
    alarms = fields.One2Many('calendar.event.alarm', 'event', 'Alarms')
    rdates = fields.One2Many('calendar.event.rdate', 'event',
        'Recurrence Dates',
        states={
            'invisible': Bool(Eval('parent')),
            }, depends=['parent'])
    rrules = fields.One2Many('calendar.event.rrule', 'event',
        'Recurrence Rules',
        states={
            'invisible': Bool(Eval('parent')),
            }, depends=['parent'])
    exdates = fields.One2Many('calendar.event.exdate', 'event',
        'Exception Dates',
        states={
            'invisible': Bool(Eval('parent')),
            }, depends=['parent'])
    exrules = fields.One2Many('calendar.event.exrule', 'event',
        'Exception Rules',
        states={
            'invisible': Bool(Eval('parent')),
            }, depends=['parent'])
    occurences = fields.One2Many('calendar.event', 'parent', 'Occurences',
        domain=[
            ('uuid', '=', Eval('uuid')),
            ('calendar', '=', Eval('calendar')),
            ],
        states={
            'invisible': Bool(Eval('parent')),
            }, depends=['uuid', 'calendar', 'parent'])
    parent = fields.Many2One('calendar.event', 'Parent',
        domain=[
            ('uuid', '=', Eval('uuid')),
            ('parent', '=', None),
            ('calendar', '=', Eval('calendar')),
            ],
        ondelete='CASCADE', depends=['uuid', 'calendar'])
    recurrence = fields.DateTime('Recurrence', select=True, states={
            'invisible': ~Eval('_parent_parent'),
            'required': Bool(Eval('_parent_parent')),
            }, depends=['parent'])
    calendar_owner = fields.Function(fields.Many2One('res.user', 'Owner'),
            'get_calendar_field', searcher='search_calendar_field')
    calendar_read_users = fields.Function(fields.Many2One('res.user',
        'Read Users'), 'get_calendar_field', searcher='search_calendar_field')
    calendar_write_users = fields.Function(fields.One2Many('res.user', None,
        'Write Users'), 'get_calendar_field', searcher='search_calendar_field')
    vevent = fields.Binary('vevent')

    @classmethod
    def __setup__(cls):
        super(Event, cls).__setup__()
        cls._sql_constraints = [
            ('uuid_recurrence_uniq', 'UNIQUE(uuid, calendar, recurrence)',
                'UUID and recurrence must be unique in a calendar!'),
            ]
        cls._constraints += [
            ('check_recurrence', 'invalid_recurrence'),
            ]
        cls._error_messages.update({
                'invalid_recurrence': 'Recurrence can not be recurrent!',
                })

    @classmethod
    def __register__(cls, module_name):
        # Migrate from 1.4: remove classification_public
        ModelData = Pool().get('ir.model.data')
        Rule = Pool().get('ir.rule')
        with Transaction().set_user(0):
            models_data = ModelData.search([
                    ('fs_id', '=', 'rule_group_read_calendar_line3'),
                    ('module', '=', module_name),
                    ('inherit', '=', None),
                    ], limit=1)
            if models_data:
                model_data, = models_data
                Rule.delete([Rule(model_data.db_id)])
        return super(Event, cls).__register__(module_name)

    @staticmethod
    def default_uuid():
        return str(uuid.uuid4())

    @staticmethod
    def default_sequence():
        return 0

    @staticmethod
    def default_classification():
        return 'public'

    @staticmethod
    def default_transp():
        return 'opaque'

    @staticmethod
    def default_timezone():
        User = Pool().get('res.user')
        user = User(Transaction().user)
        return user.timezone

    @staticmethod
    def timezones():
        return [(x, x) for x in pytz.common_timezones] + [('', '')]

    def get_calendar_field(self, name):
        assert name in ('calendar_owner', 'calendar_read_users',
                'calendar_write_users'), 'Invalid name'
        name = name[9:]
        if name in ('read_users', 'write_users'):
            return [x.id for x in getattr(self.calendar, name)]
        else:
            return getattr(self.calendar, name).id

    @classmethod
    def search_calendar_field(cls, name, clause):
        return [('calendar.' + name[9:],) + tuple(clause[1:])]

    def check_recurrence(self):
        '''
        Check the recurrence is not recurrent.
        '''
        if self.parent:
            if self.rdates \
                    or self.rrules \
                    or self.exdates \
                    or self.exrules \
                    or self.occurences:
                return False
        return True

    @classmethod
    def create(cls, values):
        pool = Pool()
        Calendar = pool.get('calendar.calendar')
        Collection = pool.get('webdav.collection')

        event = super(Event, cls).create(values)
        if (event.calendar.owner
                and (event.organizer == event.calendar.owner.email
                    or (event.parent
                        and event.parent.organizer == \
                            event.parent.calendar.owner.email))):
            if event.organizer == event.calendar.owner.email:
                attendee_emails = [x.email for x in event.attendees
                        if x.status != 'declined'
                        and x.email != event.organizer]
            else:
                attendee_emails = [x.email for x in event.parent.attendees
                        if x.status != 'declined'
                        and x.email != event.parent.organizer]
            if attendee_emails:
                with Transaction().set_user(0):
                    calendars = Calendar.search([
                            ('owner.email', 'in', attendee_emails),
                            ])
                    if not event.recurrence:
                        for calendar in calendars:
                            new_event, = cls.copy([event], default={
                                    'calendar': calendar.id,
                                    'occurences': None,
                                    })
                            for occurence in event.occurences:
                                cls.copy([occurence], default={
                                    'calendar': calendar.id,
                                    'parent': new_event.id,
                                    })
                    else:
                        parents = cls.search([
                                ('uuid', '=', event.uuid),
                                ('calendar.owner.email', 'in',
                                    attendee_emails),
                                ('id', '!=', event.id),
                                ('recurrence', '=', None),
                                ])
                        for parent in parents:
                            cls.copy([event], default={
                                    'calendar': parent.calendar.id,
                                    'parent': parent.id,
                                    })
        # Restart the cache for event
        Collection._event_cache.clear()
        return event

    def _event2update(self):
        return {
            'summary': self.summary,
            'description': self.description,
            'all_day': self.all_day,
            'dtstart': self.dtstart,
            'dtend': self.dtend,
            'location': self.location.id if self.location else None,
            'status': self.status,
            'organizer': self.organizer,
            'rdates': [('delete_all',)] + [('create', rdate._date2update())
                for rdate in self.rdates],
            'exdates': [('delete_all',)] + [('create', exdate._date2update())
                for exdate in self.exdates],
            'rrules': [('delete_all',)] + [('create', rrule._date2update())
                for rrule in self.rrules],
            'exrules': [('delete_all',)] + [('create', exrule._date2update())
                for exrule in self.exrules],
            }

    @classmethod
    def write(cls, events, values):
        pool = Pool()
        Calendar = pool.get('calendar.calendar')
        Collection = pool.get('webdav.collection')
        cursor = Transaction().cursor

        values = values.copy()
        if 'sequence' in values:
            del values['sequence']

        super(Event, cls).write(events, values)

        for i in range(0, len(events), cursor.IN_MAX):
            sub_ids = map(int, events[i:i + cursor.IN_MAX])
            red_sql, red_ids = reduce_ids('id', sub_ids)
            cursor.execute('UPDATE "' + cls._table + '" '
                'SET sequence = sequence + 1 '
                'WHERE ' + red_sql, red_ids)

        if not values:
            return
        for event in events:
            if event.calendar.owner \
                    and (event.organizer == event.calendar.owner.email \
                    or (event.parent \
                    and event.parent.organizer == event.calendar.owner.email)):
                if event.organizer == event.calendar.owner.email:
                    attendee_emails = [x.email for x in event.attendees
                            if x.status != 'declined'
                            and x.email != event.organizer]
                else:
                    attendee_emails = [x.email for x in event.parent.attendees
                            if x.status != 'declined'
                            and x.email != event.parent.organizer]
                with Transaction().set_user(0):
                    events2 = cls.search([
                            ('uuid', '=', event.uuid),
                            ('id', '!=', event.id),
                            ('recurrence', '=', event.recurrence),
                            ])
                    for event2 in events2:
                        if event2.calendar.owner.email in attendee_emails:
                            attendee_emails.remove(
                                    event2.calendar.owner.email)
                        else:
                            events2.remove(event2)
                            cls.delete([event2])
                    if events2:
                        cls.write(events2, event._event2update())
                if attendee_emails:
                    with Transaction().set_user(0):
                        calendars = Calendar.search([
                                ('owner.email', 'in', attendee_emails),
                                ])
                        if not event.recurrence:
                            for calendar in calendars:
                                new_event, = cls.copy([event], default={
                                        'calendar': calendar.id,
                                        'occurences': None,
                                        })
                                for occurence in event.occurences:
                                    cls.copy([occurence], default={
                                            'calendar': calendar.id,
                                            'parent': new_event.id,
                                            })
                        else:
                            parents = cls.search([
                                    ('uuid', '=', event.uuid),
                                    ('calendar.owner.email', 'in',
                                        attendee_emails),
                                    ('id', '!=', event.id),
                                    ('recurrence', '=', None),
                                    ])
                            for parent in parents:
                                cls.copy([event], default={
                                        'calendar': parent.calendar.id,
                                        'parent': parent.id,
                                        })
        # Restart the cache for event
        Collection._event_cache.clear()

    @classmethod
    def copy(cls, events, default=None):
        if default is None:
            default = {}

        new_events = []
        for event in events:
            current_default = default.copy()
            current_default['uuid'] = cls.default_uuid()
            new_events.append(super(Event, cls).copy([event],
                    default=current_default))
        return new_events

    @classmethod
    def delete(cls, events):
        pool = Pool()
        Attendee = pool.get('calendar.event.attendee')
        Collection = pool.get('webdav.collection')

        for event in events:
            if event.calendar.owner \
                    and (event.organizer == event.calendar.owner.email \
                    or (event.parent \
                    and event.parent.organizer == event.calendar.owner.email)):
                if event.organizer == event.calendar.owner.email:
                    attendee_emails = [x.email for x in event.attendees
                            if x.email != event.organizer]
                else:
                    attendee_emails = [x.email for x in event.parent.attendees
                            if x.email != event.parent.organizer]
                if attendee_emails:
                    with Transaction().set_user(0):
                        cls.delete(cls.search([
                                    ('uuid', '=', event.uuid),
                                    ('calendar.owner.email', 'in',
                                        attendee_emails),
                                    ('id', '!=', event.id),
                                    ('recurrence', '=', event.recurrence),
                                    ]))
            elif event.organizer \
                    or (event.parent and event.parent.organizer):
                if event.organizer:
                    organizer = event.organizer
                else:
                    organizer = event.parent.organizer
                with Transaction().set_user(0):
                    events2 = cls.search([
                            ('uuid', '=', event.uuid),
                            ('calendar.owner.email', '=', organizer),
                            ('id', '!=', event.id),
                            ('recurrence', '=', event.recurrence),
                            ], limit=1)
                    if events2:
                        event2, = events2
                        for attendee in event2.attendees:
                            if attendee.email == event.calendar.owner.email:
                                Attendee.write([attendee], {
                                        'status': 'declined',
                                        })
        super(Event, cls).delete(events)
        # Restart the cache for event
        Collection._event_cache.clear()

    @classmethod
    def ical2values(cls, event_id, ical, calendar_id, vevent=None):
        '''
        Convert iCalendar to values for create or write with
        the event id for write or None for create
        '''
        pool = Pool()
        Category = pool.get('calendar.category')
        Location = pool.get('calendar.location')
        Rdate = pool.get('calendar.event.rdate')
        Exdate = pool.get('calendar.event.exdate')
        Rrule = pool.get('calendar.rrule')
        Exrule = pool.get('calendar.event.exrule')
        Valarm = pool.get('calendar.event.valarm')

        vevents = []
        if not vevent:
            vevent = ical.vevent

            for i in ical.getChildren():
                if i.name == 'VEVENT' \
                        and i != vevent:
                    vevents.append(i)

        event = None
        if event_id:
            event = cls(event_id)

        res = {}
        if not event:
            if hasattr(vevent, 'uid'):
                res['uuid'] = vevent.uid.value
            else:
                res['uuid'] = str(uuid.uuid4())
        if hasattr(vevent, 'summary'):
            res['summary'] = vevent.summary.value
        else:
            res['summary'] = None
        if hasattr(vevent, 'description'):
            res['description'] = vevent.description.value
        else:
            res['description'] = None
        if not isinstance(vevent.dtstart.value, datetime.datetime):
            res['all_day'] = True
            res['dtstart'] = datetime.datetime.combine(vevent.dtstart.value,
                    datetime.time())
        else:
            res['all_day'] = False
            if vevent.dtstart.value.tzinfo:
                res['dtstart'] = vevent.dtstart.value.astimezone(tzlocal)
            else:
                res['dtstart'] = vevent.dtstart.value
        if hasattr(vevent, 'dtend'):
            if not isinstance(vevent.dtend.value, datetime.datetime):
                res['dtend'] = datetime.datetime.combine(vevent.dtend.value,
                        datetime.time())
            else:
                if vevent.dtend.value.tzinfo:
                    res['dtend'] = vevent.dtend.value.astimezone(tzlocal)
                else:
                    res['dtend'] = vevent.dtend.value
        elif hasattr(vevent, 'duration') and hasattr(vevent, 'dtstart'):
            res['dtend'] = vevent.dtstart.value + vevent.duration.value
        else:
            res['dtend'] = None
        if hasattr(vevent, 'recurrence-id'):
            if not isinstance(vevent.recurrence_id.value, datetime.datetime):
                res['recurrence'] = datetime.datetime.combine(
                        vevent.recurrence_id.value, datetime.time()
                        ).replace(tzinfo=tzlocal)
            else:
                if vevent.recurrence_id.value.tzinfo:
                    res['recurrence'] = \
                            vevent.recurrence_id.value.astimezone(tzlocal)
                else:
                    res['recurrence'] = vevent.recurrence_id.value
        else:
            res['recurrence'] = None
        if hasattr(vevent, 'status'):
            res['status'] = vevent.status.value.lower()
        else:
            res['status'] = ''
        if hasattr(vevent, 'categories'):
            with Transaction().set_context(active_test=False):
                categories = Category.search([
                        ('name', 'in', [x for x in vevent.categories.value]),
                        ])
            category_names2ids = {}
            for category in categories:
                category_names2ids[category.name] = category.id
            for category in vevent.categories.value:
                if category not in category_names2ids:
                    categories.append(Category.create({
                                'name': category,
                                }))
            res['categories'] = [('set', map(int, categories))]
        else:
            res['categories'] = [('unlink_all',)]
        if hasattr(vevent, 'class'):
            if getattr(vevent, 'class').value.lower() in \
                    dict(cls.classification.selection):
                res['classification'] = getattr(vevent, 'class').value.lower()
            else:
                res['classification'] = 'public'
        else:
            res['classification'] = 'public'
        if hasattr(vevent, 'location'):
            with Transaction().set_context(active_test=False):
                locations = Location.search([
                        ('name', '=', vevent.location.value),
                        ], limit=1)
            if not locations:
                location = Location.create({
                        'name': vevent.location.value,
                        })
            else:
                location, = locations
            res['location'] = location.id
        else:
            res['location'] = None

        res['calendar'] = calendar_id

        if hasattr(vevent, 'transp'):
            res['transp'] = vevent.transp.value.lower()
        else:
            res['transp'] = 'opaque'

        if hasattr(vevent, 'organizer'):
            if vevent.organizer.value.lower().startswith('mailto:'):
                res['organizer'] = vevent.organizer.value[7:]
            else:
                res['organizer'] = vevent.organizer.value
        else:
            res['organizer'] = None

        attendees_todel = {}
        if event:
            for attendee in event.attendees:
                attendees_todel[attendee.email] = attendee.id
        res['attendees'] = []
        if hasattr(vevent, 'attendee'):
            while vevent.attendee_list:
                attendee = vevent.attendee_list.pop()
                vals = Attendee.attendee2values(attendee)
                if vals['email'] in attendees_todel:
                    res['attendees'].append(('write',
                        attendees_todel[vals['email']], vals))
                    del attendees_todel[vals['email']]
                else:
                    res['attendees'].append(('create', vals))
        res['attendees'].append(('delete', attendees_todel.values()))

        res['rdates'] = []
        if event:
            res['rdates'].append(('delete', [x.id for x in event.rdates]))
        if hasattr(vevent, 'rdate'):
            while vevent.rdate_list:
                rdate = vevent.rdate_list.pop()
                for rdate in rdate.value:
                    res['rdates'].append(('create', Rdate.date2values(rdate)))

        res['exdates'] = []
        if event:
            res['exdates'].append(('delete', [x.id for x in event.exdates]))
        if hasattr(vevent, 'exdate'):
            while vevent.exdate_list:
                exdate = vevent.exdate_list.pop()
                for exdate in exdate.value:
                    res['exdates'].append(('create',
                            Exdate.date2values(exdate)))

        res['rrules'] = []
        if event:
            res['rrules'].append(('delete', [x.id for x in event.rrules]))
        if hasattr(vevent, 'rrule'):
            while vevent.rrule_list:
                rrule = vevent.rrule_list.pop()
                res['rrules'].append(('create', Rrule.rule2values(rrule)))

        res['exrules'] = []
        if event:
            res['exrules'].append(('delete', [x.id for x in event.exrules]))
        if hasattr(vevent, 'exrule'):
            while vevent.exrule_list:
                exrule = vevent.exrule_list.pop()
                res['exrules'].append(('create', Exrule.rule2values(exrule)))

        if event:
            res.setdefault('alarms', [])
            res['alarms'].append(('delete', [x.id for x in event.alarms]))
        if hasattr(vevent, 'valarm'):
            res.setdefault('alarms', [])
            while vevent.valarm_list:
                valarm = vevent.valarm_list.pop()
                res['alarms'].append(('create', Valarm.valarm2values(valarm)))

        if hasattr(ical, 'vtimezone'):
            if ical.vtimezone.tzid.value in pytz.common_timezones:
                res['timezone'] = ical.vtimezone.tzid.value
            else:
                for timezone in pytz.common_timezones:
                    if ical.vtimezone.tzid.value.endswith(timezone):
                        res['timezone'] = timezone

        res['vevent'] = vevent.serialize()

        occurences_todel = []
        if event:
            occurences_todel = [x.id for x in event.occurences]
        for vevent in vevents:
            event_id = None
            vals = cls.ical2values(event_id, ical, calendar_id, vevent=vevent)
            if event:
                for occurence in event.occurences:
                    if vals['recurrence'] == \
                            occurence.recurrence.replace(tzinfo=tzlocal):
                        event_id = occurence.id
                        occurences_todel.remove(occurence.id)
            if event:
                vals['uuid'] = event.uuid
            else:
                vals['uuid'] = res['uuid']
            res.setdefault('occurences', [])
            if event_id:
                res['occurences'].append(('write', event_id, vals))
            else:
                res['occurences'].append(('create', vals))
        if occurences_todel:
            res.setdefault('occurences', [])
            res['occurences'].insert(0, ('delete', occurences_todel))
        return res

    def event2ical(self):
        '''
        Return an iCalendar instance of vobject for event
        '''
        pool = Pool()
        User = pool.get('res.user')

        user = User(Transaction().user)
        if self.timezone:
            tzevent = pytz.timezone(self.timezone)
            tzevent = dateutil.tz.gettz(self.timezone)
        elif user.timezone:
            tzevent = dateutil.tz.gettz(user.timezone)
        else:
            tzevent = tzlocal

        ical = vobject.iCalendar()
        vevent = ical.add('vevent')
        if self.vevent:
            ical.vevent = vobject.readOne(str(self.vevent))
            vevent = ical.vevent
            ical.vevent.transformToNative()
        if self.summary:
            if not hasattr(vevent, 'summary'):
                vevent.add('summary')
            vevent.summary.value = self.summary
        elif hasattr(vevent, 'summary'):
            del vevent.summary
        if self.description:
            if not hasattr(vevent, 'description'):
                vevent.add('description')
            vevent.description.value = self.description
        elif hasattr(vevent, 'description'):
            del vevent.description
        if not hasattr(vevent, 'dtstart'):
            vevent.add('dtstart')
        if self.all_day:
            vevent.dtstart.value = self.dtstart.date()
        else:
            vevent.dtstart.value = self.dtstart.replace(tzinfo=tzlocal)\
                    .astimezone(tzevent)
        if self.dtend:
            if not hasattr(vevent, 'dtend'):
                vevent.add('dtend')
            if self.all_day:
                vevent.dtend.value = self.dtend.date()
            else:
                vevent.dtend.value = self.dtend.replace(tzinfo=tzlocal)\
                        .astimezone(tzevent)
        elif hasattr(vevent, 'dtend'):
            del vevent.dtend
        if not hasattr(vevent, 'created'):
            vevent.add('created')
        vevent.created.value = self.create_date.replace(tzinfo=tzlocal)
        if not hasattr(vevent, 'dtstamp'):
            vevent.add('dtstamp')
        date = self.write_date or self.create_date
        vevent.dtstamp.value = date.replace(tzinfo=tzlocal)
        if not hasattr(vevent, 'last-modified'):
            vevent.add('last-modified')
        vevent.last_modified.value = date.replace(tzinfo=tzlocal)
        if self.recurrence and self.parent:
            if not hasattr(vevent, 'recurrence-id'):
                vevent.add('recurrence-id')
            if self.all_day:
                vevent.recurrence_id.value = self.recurrence.date()
            else:
                vevent.recurrence_id.value = self.recurrence\
                        .replace(tzinfo=tzlocal).astimezone(tzevent)
        elif hasattr(vevent, 'recurrence-id'):
            del vevent.recurrence_id
        if self.status:
            if not hasattr(vevent, 'status'):
                vevent.add('status')
            vevent.status.value = self.status.upper()
        elif hasattr(vevent, 'status'):
            del vevent.status
        if not hasattr(vevent, 'uid'):
            vevent.add('uid')
        vevent.uid.value = self.uuid
        if not hasattr(vevent, 'sequence'):
            vevent.add('sequence')
        vevent.sequence.value = str(self.sequence) or '0'
        if self.categories:
            if not hasattr(vevent, 'categories'):
                vevent.add('categories')
            vevent.categories.value = [x.name for x in self.categories]
        elif hasattr(vevent, 'categories'):
            del vevent.categories
        if not hasattr(vevent, 'class'):
            vevent.add('class')
            getattr(vevent, 'class').value = self.classification.upper()
        elif getattr(vevent, 'class').value.lower() in \
                dict(self.__class__.classification.selection):
            getattr(vevent, 'class').value = self.classification.upper()
        if self.location:
            if not hasattr(vevent, 'location'):
                vevent.add('location')
            vevent.location.value = self.location.name
        elif hasattr(vevent, 'location'):
            del vevent.location

        if not hasattr(vevent, 'transp'):
            vevent.add('transp')
        vevent.transp.value = self.transp.upper()

        if self.organizer:
            if not hasattr(vevent, 'organizer'):
                vevent.add('organizer')
            vevent.organizer.value = 'MAILTO:' + self.organizer
        elif hasattr(vevent, 'organizer'):
            del vevent.organizer

        vevent.attendee_list = []
        for attendee in self.attendees:
            vevent.attendee_list.append(attendee.attendee2attendee())

        if self.rdates:
            vevent.add('rdate')
            vevent.rdate.value = []
            for rdate in self.rdates:
                vevent.rdate.value.append(rdate.date2date())

        if self.exdates:
            vevent.add('exdate')
            vevent.exdate.value = []
            for exdate in self.exdates:
                vevent.exdate.value.append(exdate.date2date())

        if self.rrules:
            for rrule in self.rrules:
                vevent.add('rrule').value = rrule.rule2rule()

        if self.exrules:
            for exrule in self.exrules:
                vevent.add('exrule').value = exrule.rule2rule()

        vevent.valarm_list = []
        for alarm in self.alarms:
            valarm = alarm.alarm2valarm()
            if valarm:
                vevent.valarm_list.append(valarm)

        for occurence in self.occurences:
            oical = occurence.event2ical()
            ical.vevent_list.append(oical.vevent)
        return ical


class EventCategory(ModelSQL):
    'Event - Category'
    __name__ = 'calendar.event-calendar.category'
    event = fields.Many2One('calendar.event', 'Event', ondelete='CASCADE',
            required=True, select=True)
    category = fields.Many2One('calendar.category', 'Category',
            ondelete='CASCADE', required=True, select=True)


class Alarm(ModelSQL):
    'Alarm'
    __name__ = 'calendar.alarm'
    valarm = fields.Binary('valarm')

    @classmethod
    def valarm2values(cls, valarm):
        '''
        Convert a valarm object into values for create or write
        '''
        return {
            'valarm': valarm.serialize(),
            }

    def alarm2valarm(self):
        '''
        Return a valarm instance of vobject for alarm
        '''
        if self.valarm:
            return vobject.readOne(str(self.valarm))


class EventAlarm(ModelSQL):
    'Alarm'
    __name__ = 'calendar.event.alarm'
    _inherits = {'calendar.alarm': 'calendar_alarm'}
    calendar_alarm = fields.Many2One('calendar.alarm', 'Calendar Alarm',
            required=True, ondelete='CASCADE', select=True)
    event = fields.Many2One('calendar.event', 'Event', ondelete='CASCADE',
            required=True, select=True)

    @classmethod
    def create(cls, values):
        Event = Pool().get('calendar.event')
        if values.get('event'):
            # Update write_date of event
            Event.write(values['event'], {})
        return super(EventAlarm, cls).create(values)

    @classmethod
    def write(cls, event_alarms, values):
        Event = Pool().get('calendar.event')
        events = [x.event for x in event_alarms]
        if values.get('event'):
            events.append(Event(values['event']))
        if events:
            # Update write_date of event
            Event.write(events, {})
        return super(EventAlarm, cls).write(event_alarms, values)

    @classmethod
    def delete(cls, event_alarms):
        pool = Pool()
        Event = pool.get('calendar.event')
        Alarm = pool.get('calendar.alarm')
        alarms = [a.calendar_alarm for a in event_alarms]
        events = [x.event for x in event_alarms]
        if events:
            # Update write_date of event
            Event.write(events, {})
        super(EventAlarm, cls).delete(event_alarms)
        if alarms:
            Alarm.delete(alarms)

    @classmethod
    def valarm2values(cls, alarm):
        Alarm = Pool().get('calendar.alarm')
        return Alarm.valarm2values(alarm)

    def alarm2valarm(self):
        return self.calendar_alarm.alarm2valarm()


class Attendee(ModelSQL, ModelView):
    'Attendee'
    __name__ = 'calendar.attendee'
    email = fields.Char('Email', required=True, states={
        'readonly': Eval('id', 0) > 0,
        }, depends=['id'])
    status = fields.Selection([
        ('', ''),
        ('needs-action', 'Needs Action'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('tentative', 'Tentative'),
        ('delegated', 'Delegated'),
        ], 'Participation Status')
    attendee = fields.Binary('attendee')

    @staticmethod
    def default_status():
        return ''

    def _attendee2update(self):
        return {
            'status': self.status,
            }

    @classmethod
    def attendee2values(cls, attendee):
        '''
        Convert a attendee object into values for create or write
        '''
        res = {}
        if attendee.value.lower().startswith('mailto:'):
            res['email'] = attendee.value[7:]
        else:
            res['email'] = attendee.value
        res['status'] = ''
        if hasattr(attendee, 'partstat_param'):
            if attendee.partstat_param.lower() in dict(cls.status.selection):
                res['status'] = attendee.partstat_param.lower()
        res['attendee'] = attendee.serialize()
        return res

    def attendee2attendee(self):
        '''
        Return a attendee instance of vobject for attendee
        '''
        res = None
        if self.attendee:
            res = vobject.base.textLineToContentLine(
                    str(self.attendee).replace('\r\n ', ''))
        else:
            res = vobject.base.ContentLine('ATTENDEE', [], '')

        if self.status:
            if hasattr(res, 'partstat_param'):
                if res.partstat_param.lower() in dict(self.status.selection):
                    res.partstat_param = self.status.upper()
            else:
                res.partstat_param = self.status.upper()
        elif hasattr(res, 'partstat_param'):
            if res.partstat_param.lower() in dict(self.status.selection):
                del res.partstat_param

        res.value = 'MAILTO:' + self.email
        return res


class EventAttendee(ModelSQL, ModelView):
    'Attendee'
    __name__ = 'calendar.event.attendee'
    _inherits = {'calendar.attendee': 'calendar_attendee'}
    calendar_attendee = fields.Many2One('calendar.attendee',
        'Calendar Attendee', required=True, ondelete='CASCADE', select=True)
    event = fields.Many2One('calendar.event', 'Event', ondelete='CASCADE',
        required=True, select=True)

    @staticmethod
    def create(cls, values):
        Event = Pool().get('calendar.event')
        if values.get('event'):
            # Update write_date of event
            Event.write([Event(values['event'])], {})
        event_attendee = super(EventAttendee, cls).create(values)
        event = event_attendee.event
        if (event.calendar.owner
                and (event.organizer == event.calendar.owner.email
                    or (event.parent
                        and event.parent.organizer == \
                            event.parent.calendar.owner.email))):
            if event.organizer == event.calendar.owner.email:
                attendee_emails = [x.email for x in event.attendees
                        if x.email != event.organizer]
            else:
                attendee_emails = [x.email for x in event.parent.attendees
                        if x.email != event.parent.organizer]
            if attendee_emails:
                with Transaction().set_user(0):
                    events = Event.search([
                            ('uuid', '=', event.uuid),
                            ('calendar.owner.email', 'in', attendee_emails),
                            ('id', '!=', event.id),
                            ('recurrence', '=', event.recurrence),
                            ])
                    for event in events:
                        cls.copy([event_attendee], default={
                                'event': event.id,
                                })
        return event_attendee

    @classmethod
    def write(cls, event_attendees, values):
        Event = Pool().get('calendar.event')
        events = [x.event.id for x in event_attendees]
        if values.get('event'):
            events.append(Event(values['event']))
        if events:
            # Update write_date of event
            Event.write(events, {})

        if 'email' in values:
            values = values.copy()
            del values['email']

        super(EventAttendee, cls).write(event_attendees, values)
        for event_attendee in event_attendees:
            event = event_attendee.event
            if event.calendar.owner \
                    and (event.organizer == event.calendar.owner.email \
                    or (event.parent \
                    and event.parent.organizer == event.calendar.owner.email)):
                if event.organizer == event.calendar.owner.email:
                    attendee_emails = [x.email for x in event.attendees
                            if x.email != event.organizer]
                else:
                    attendee_emails = [x.email for x in event.parent.attendees
                            if x.email != event.parent.organizer]
                if attendee_emails:
                    with Transaction().set_user(0):
                        other_attendees = cls.search([
                                ('event.uuid', '=', event.uuid),
                                ('event.calendar.owner.email', 'in',
                                    attendee_emails),
                                ('id', '!=', event_attendee.id),
                                ('event.recurrence', '=',
                                    event.recurrence),
                                ('email', '=', event_attendee.email),
                                ])
                        cls.write(other_attendees,
                            event_attendee._attendee2update())

    @classmethod
    def delete(cls, event_attendees):
        pool = Pool()
        Event = pool.get('calendar.event')
        Attendee = pool.get('calendar.attendee')

        calendar_attendees = [a.calendar_attendee for a in event_attendees]
        events = [x.event for x in event_attendees]
        if events:
            # Update write_date of event
            Event.write(events, {})

        for attendee in event_attendees:
            event = attendee.event
            if event.calendar.owner \
                    and (event.organizer == event.calendar.owner.email \
                    or (event.parent \
                    and event.parent.organizer == event.calendar.owner.email)):
                if event.organizer == event.calendar.owner.email:
                    attendee_emails = [x.email for x in event.attendees
                            if x.email != event.organizer]
                else:
                    attendee_emails = [x.email for x in event.parent.attendees
                            if x.email != event.parent.organizer]
                if attendee_emails:
                    with Transaction().set_user(0):
                        attendees = cls.search([
                                ('event.uuid', '=', event.uuid),
                                ('event.calendar.owner.email', 'in',
                                    attendee_emails),
                                ('id', '!=', attendee.id),
                                ('event.recurrence', '=',
                                    event.recurrence),
                                ('email', '=', attendee.email),
                                ])
                        cls.delete(attendees)
            elif event.calendar.owner \
                    and ((event.organizer \
                    or (event.parent and event.parent.organizer)) \
                    and attendee.email == event.calendar.owner.email):
                if event.organizer:
                    organizer = event.organizer
                else:
                    organizer = event.parent.organizer
                with Transaction().set_user(0):
                    attendees = cls.search([
                            ('event.uuid', '=', event.uuid),
                            ('event.calendar.owner.email', '=', organizer),
                            ('id', '!=', attendee.id),
                            ('event.recurrence', '=', event.recurrence),
                            ('email', '=', attendee.email),
                            ])
                    if attendees:
                        cls.write(attendees, {
                                'status': 'declined',
                                })
        super(EventAttendee, cls).delete(event_attendees)
        if calendar_attendees:
            Attendee.delete(calendar_attendees)

    @classmethod
    def copy(cls, event_attendees, default=None):
        Attendee = Pool().get('calendar.attendee')

        if default is None:
            default = {}
        default = default.copy()
        new_event_attendees = []
        for event_attendee in event_attendees:
            default['calendar_attendee'], = Attendee.copy(
                    [event_attendee.calendar_attendee])
            new_event_attendees.extend(super(EventAttendee, cls).copy(
                    [event_attendee], default=default))
        return new_event_attendees

    def _attendee2update(self):
        return self.calendar_attendee._attendee2update()

    @staticmethod
    def attendee2values(attendee):
        Attendee = Pool().get('calendar.attendee')
        return Attendee.attendee2values(attendee)

    def attendee2attendee(self):
        return self.calendar_attendee.attendee2attendee()


class Date(ModelSQL, ModelView):
    'Calendar Date'
    __name__ = 'calendar.date'
    _rec_name = 'datetime'
    date = fields.Boolean('Is Date', help='Ignore time of field "Date", ' \
            'but handle as date only.')
    datetime = fields.DateTime('Date', required=True)

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().cursor
        # Migration from 1.4: calendar.rdate renamed to calendar.date
        old_table = 'calendar_rdate'
        if TableHandler.table_exist(cursor, old_table):
            TableHandler.table_rename(cursor, old_table, cls._table)

        super(Date, cls).__register__(module_name)

    def _date2update(self):
        return {
            'date': self.date,
            'datetime': self.datetime,
            }

    @staticmethod
    def date2values(date):
        '''
        Convert a date object into values for create or write
        '''
        res = {}
        if not isinstance(date, datetime.datetime):
            res['date'] = True
            res['datetime'] = datetime.datetime.combine(date,
                    datetime.time())
        else:
            res['date'] = False
            if date.tzinfo:
                res['datetime'] = date.astimezone(tzlocal)
            else:
                res['datetime'] = date
        return res

    def date2date(self):
        '''
        Return a datetime for date
        '''
        if self.date:
            return self.date.datetime.date()
        else:
            # Convert to UTC as sunbird doesn't handle tzid
            return self.datetime.replace(tzinfo=tzlocal).astimezone(tzutc)


class EventRDate(ModelSQL, ModelView):
    'Recurrence Date'
    __name__ = 'calendar.event.rdate'
    _inherits = {'calendar.date': 'calendar_date'}
    _rec_name = 'datetime'
    calendar_date = fields.Many2One('calendar.date', 'Calendar Date',
            required=True, ondelete='CASCADE', select=True)
    event = fields.Many2One('calendar.event', 'Event', ondelete='CASCADE',
            select=True, required=True)

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().cursor
        # Migration from 1.4: calendar_rdate renamed to calendar_date
        table = TableHandler(cursor, cls, module_name)
        old_column = 'calendar_rdate'
        if table.column_exist(old_column):
            table.column_rename(old_column, 'calendar_date')

        super(EventRDate, cls).__register__(module_name)

    @classmethod
    def create(cls, values):
        Event = Pool().get('calendar.event')
        if values.get('event'):
            # Update write_date of event
            Event.write([Event(values['event'])], {})
        return super(EventRDate, cls).create(values)

    @classmethod
    def write(cls, event_rdates, values):
        Event = Pool().get('calendar.event')
        events = [x.event for x in event_rdates]
        if values.get('event'):
            events.append(Event(values['event']))
        if events:
            # Update write_date of event
            Event.write(events, {})
        super(EventRDate, cls).write(event_rdates, values)

    @classmethod
    def delete(cls, event_rdates):
        pool = Pool()
        Event = pool.get('calendar.event')
        Date = pool.get('calendar.date')
        dates = [a.calendar_date for a in event_rdates]
        events = [x.event for x in event_rdates]
        if events:
            # Update write_date of event
            Event.write(events, {})
        super(EventRDate, cls).delete(event_rdates)
        if dates:
            Date.delete(dates)

    def _date2update(self):
        return self.calendar_date._date2update()

    @classmethod
    def date2values(cls, date):
        Date = Pool().get('calendar.date')
        return Date.date2values(date)

    def date2date(self):
        return self.calendar_date.date2date()


class EventExDate(EventRDate):
    'Exception Date'
    __name__ = 'calendar.event.exdate'


class RRule(ModelSQL, ModelView):
    'Recurrence Rule'
    __name__ = 'calendar.rrule'
    _rec_name = 'freq'

    freq = fields.Selection([
        ('secondly', 'Secondly'),
        ('minutely', 'Minutely'),
        ('hourly', 'Hourly'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
        ], 'Frequency', required=True)
    until_date = fields.Boolean('Is Date', help='Ignore time of field ' \
            '"Until Date", but handle as date only.')
    until = fields.DateTime('Until Date')
    count = fields.Integer('Count')
    interval = fields.Integer('Interval')
    bysecond = fields.Char('By Second')
    byminute = fields.Char('By Minute')
    byhour = fields.Char('By Hour')
    byday = fields.Char('By Day')
    bymonthday = fields.Char('By Month Day')
    byyearday = fields.Char('By Year Day')
    byweekno = fields.Char('By Week Number')
    bymonth = fields.Char('By Month')
    bysetpos = fields.Char('By Position')
    wkst = fields.Selection([
        ('su', 'Sunday'),
        ('mo', 'Monday'),
        ('tu', 'Tuesday'),
        ('we', 'Wednesday'),
        ('th', 'Thursday'),
        ('fr', 'Friday'),
        ('sa', 'Saturday'),
        ], 'Week Day', sort=False)

    @classmethod
    def __setup__(cls):
        super(RRule, cls).__setup__()
        cls._sql_constraints += [
            ('until_count_only_one',
                'CHECK(until IS NULL OR count IS NULL OR count = 0)',
                'Only one of "until" and "count" can be set!'),
            ]
        cls._constraints += [
            ('check_bysecond', 'invalid_bysecond'),
            ('check_byminute', 'invalid_byminute'),
            ('check_byhour', 'invalid_byhour'),
            ('check_byday', 'invalid_byday'),
            ('check_bymonthday', 'invalid_bymonthday'),
            ('check_byyearday', 'invalid_byyearday'),
            ('check_byweekno', 'invalid_byweekno'),
            ('check_bymonth', 'invalid_bymonth'),
            ('check_bysetpos', 'invalid_bysetpos'),
            ]
        cls._error_messages.update({
                'invalid_bysecond': 'Invalid "By Second"',
                'invalid_byminute': 'Invalid "By Minute"',
                'invalid_byhour': 'Invalid "By Hour"',
                'invalid_byday': 'Invalid "By Day"',
                'invalid_bymonthday': 'Invalid "By Month Day"',
                'invalid_byyearday': 'Invalid "By Year Day"',
                'invalid_byweekno': 'Invalid "By Week Number"',
                'invalid_bymonth': 'Invalid "By Month"',
                'invalid_bysetpos': 'Invalid "By Position"',
                })

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().cursor
        # Migrate from 1.4: unit_count replaced by until_count_only_one
        table = TableHandler(cursor, cls, module_name)
        table.drop_constraint('until_count')
        return super(RRule, cls).__register__(module_name)

    def check_bysecond(self):
        if self.bysecond:
            for second in self.bysecond.split(','):
                try:
                    second = int(second)
                except Exception:
                    return False
                if not (second >= 0 and second <= 59):
                    return False
        return True

    def check_byminute(self):
        if self.byminute:
            for minute in self.byminute.split(','):
                try:
                    minute = int(minute)
                except Exception:
                    return False
                if not (minute >= 0 and minute <= 59):
                    return False
        return True

    def check_byhour(self):
        if self.byhour:
            for hour in self.byhour.split(','):
                try:
                    hour = int(hour)
                except Exception:
                    return False
                if not (hour >= 0 and hour <= 23):
                    return False
        return True

    def check_byday(self):
        if self.byday:
            for weekdaynum in self.byday.split(','):
                weekday = weekdaynum[-2:]
                if weekday not in ('SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'):
                    return False
                ordwk = weekday[:-2]
                if not ordwk:
                    continue
                try:
                    ordwk = int(ordwk)
                except Exception:
                    return False
                if not (abs(ordwk) >= 1 and abs(ordwk) <= 53):
                    return False
        return True

    def check_bymonthday(self):
        if self.bymonthday:
            for monthdaynum in self.bymonthday.split(','):
                try:
                    monthdaynum = int(monthdaynum)
                except Exception:
                    return False
                if not (abs(monthdaynum) >= 1 and abs(monthdaynum) <= 31):
                    return False
        return True

    def check_byyearday(self):
        if self.byyearday:
            for yeardaynum in self.byyearday.split(','):
                try:
                    yeardaynum = int(yeardaynum)
                except Exception:
                    return False
                if not (abs(yeardaynum) >= 1 and abs(yeardaynum) <= 366):
                    return False
        return True

    def check_byweekno(self):
        if self.byweekno:
            for weeknum in self.byweekno.split(','):
                try:
                    weeknum = int(weeknum)
                except Exception:
                    return False
                if not (abs(weeknum) >= 1 and abs(weeknum) <= 53):
                    return False
        return True

    def check_bymonth(self):
        if self.bymonth:
            for monthnum in self.bymonth.split(','):
                try:
                    monthnum = int(monthnum)
                except Exception:
                    return False
                if not (monthnum >= 1 and monthnum <= 12):
                    return False
        return True

    def check_bysetpos(self):
        if self.bysetpos:
            for setposday in self.bysetpos.split(','):
                try:
                    setposday = int(setposday)
                except Exception:
                    return False
                if not (abs(setposday) >= 1 and abs(setposday) <= 366):
                    return False
        return True

    def _rule2update(self):
        res = {}
        for field in ('freq', 'until_date', 'until', 'count', 'interval',
                'bysecond', 'byminute', 'byhour', 'byday', 'bymonthday',
                'byyearday', 'byweekno', 'bymonth', 'bysetpos', 'wkst'):
            res[field] = getattr(self, field)
        return res

    @classmethod
    def rule2values(cls, rule):
        '''
        Convert a rule object into values for create or write
        '''
        res = {}
        for attr in str(rule.value).replace('\\', '').split(';'):
            field, value = attr.split('=')
            field = field.lower()
            if field == 'until':
                try:
                    value = vobject.icalendar.stringToDateTime(value)
                except Exception:
                    value = vobject.icalendar.stringToDate(value)
                if not isinstance(value, datetime.datetime):
                    res['until_date'] = True
                    res['until'] = datetime.datetime.combine(value,
                            datetime.time())
                else:
                    res['until_date'] = False
                    if value.tzinfo:
                        res['until'] = value.astimezone(tzlocal)
                    else:
                        res['until'] = value
            elif field in ('freq', 'wkst'):
                res[field] = value.lower()
            else:
                res[field] = value
        return res

    def rule2rule(self):
        '''
        Return a rule string for rule
        '''
        res = 'FREQ=' + self.freq.upper()
        if self.until:
            res += ';UNTIL='
            if self.until_date:
                res += vobject.icalendar.dateToString(self.until.date())
            else:
                res += vobject.icalendar.dateTimeToString(self.until\
                        .replace(tzinfo=tzlocal).astimezone(tzutc),
                        convertToUTC=True)
        elif self.count:
            res += ';COUNT=' + str(self.count)
        for field in ('freq', 'wkst'):
            if getattr(self, field):
                res += ';' + field.upper() + '=' + getattr(self, field).upper()
        for field in ('interval', 'bysecond', 'byminute', 'byhour',
                'byday', 'bymonthday', 'byyearday', 'byweekno',
                'bymonth', 'bysetpos'):
            if getattr(self, field):
                res += ';' + field.upper() + '=' + str(getattr(self, field))
        return res


class EventRRule(ModelSQL, ModelView):
    'Recurrence Rule'
    __name__ = 'calendar.event.rrule'
    _inherits = {'calendar.rrule': 'calendar_rrule'}
    _rec_name = 'freq'

    calendar_rrule = fields.Many2One('calendar.rrule', 'Calendar RRule',
            required=True, ondelete='CASCADE', select=True)
    event = fields.Many2One('calendar.event', 'Event', ondelete='CASCADE',
            select=True, required=True)

    @classmethod
    def create(cls, values):
        Event = Pool().get('calendar.event')
        if values.get('event'):
            # Update write_date of event
            Event.write([Event(values['event'])], {})
        return super(EventRRule, cls).create(values)

    @classmethod
    def write(cls, event_rrules, values):
        Event = Pool().get('calendar.event')
        events = [x.event for x in event_rrules]
        if values.get('event'):
            events.append(Event(values['event']))
        if events:
            # Update write_date of event
            Event.write(events, {})
        super(EventRRule, cls).write(event_rrules, values)

    @classmethod
    def delete(cls, event_rrules):
        pool = Pool()
        Event = pool.get('calendar.event')
        Rrule = pool.get('calendar.rrule')
        rrules = [a.calendar_rrule for a in event_rrules]
        events = [x.event for x in event_rrules]
        if events:
            # Update write_date of event
            Event.write(events, {})
        super(EventRRule, cls).delete(event_rrules)
        if rrules:
            Rrule.delete(rrules)

    def _rule2update(self):
        return self.calendar_rrule._rule2update()

    @classmethod
    def rule2values(self, rule):
        Rule = Pool().get('calendar.rrule')
        return Rule.rule2values(rule)

    def rule2rule(self):
        return self.calendar_rrule.rule2rule()


class EventExRule(EventRRule):
    'Exception Rule'
    __name__ = 'calendar.event.exrule'
    _table = 'calendar_event_exrule'  # Needed to override EventRRule._table
