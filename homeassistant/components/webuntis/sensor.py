"""Sensor platform for webuntis."""
import copy
from datetime import timedelta
import logging

import voluptuous as vol
from webuntis import Session as webuntis_session
from webuntis.errors import AuthError, BadCredentialsError

from homeassistant.components.calendar import (
    CalendarEventDevice,
    calculate_offset,
    get_date,
    is_offset_reached,
)
from homeassistant.components.sensor import ENTITY_ID_FORMAT, PLATFORM_SCHEMA
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import SERVER_SOFTWARE as HA_USER_AGENT
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.util import Throttle, dt

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=15)
LOGIN_ATTEMPTS = 3

DOMAIN = "webuntis"

CONF_SCHOOL = "school"
CONF_KLASSE = "klasse"

DEFAULT_NAME = "Webuntis"
OFFSET = "!!"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_SCHOOL): cv.string,
        vol.Required(CONF_KLASSE): cv.string,
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)


def setup_platform(hass, config, add_entities, disc_info=None):
    """Set up the Webuntis Calendar platform."""
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    school = config.get(CONF_SCHOOL)
    klasse = config.get(CONF_KLASSE)
    host = config.get(CONF_HOST)
    name = config.get(CONF_NAME)

    session = webuntis_session(
        username=username,
        password=password,
        school=school,
        server=host,
        useragent=HA_USER_AGENT,
    )

    try:
        _LOGGER.warning("trying to login")
        session.login()
    except BadCredentialsError as credentials_error:
        _LOGGER.error(
            "Incorrect credentials, please check your username and password: %s",
            credentials_error,
        )
    except AuthError as auth_error:
        _LOGGER.error(
            "Did not receive a valid session ID from the webuntis host, reason is unknown: %s",
            auth_error,
        )
    _LOGGER.warning("login successfull")
    filtered_klasse = session.klassen().filter(name=klasse)[0]

    if not filtered_klasse:
        _LOGGER.error("Could not find the specified klasse '%s'", klasse)
        return

    entity_id = generate_entity_id(ENTITY_ID_FORMAT, name, hass=hass)
    hass.states.set(entity_id, filtered_klasse.long_name)
    # add_entities(WebunitsCalendarEventDevice(name, session, klasse, entity_id), True)
    session.logout()


class WebunitsCalendarEventDevice(CalendarEventDevice):
    """WebUntis Sensor class."""

    def __init__(self, name, session, klasse, entity_id, all_day=False, search=None):
        """Initialize a webuntis sensor."""
        self.session = session
        self.klasse = klasse
        self.entity_id = entity_id
        self.data = WebuntisCalendarData(session, klasse)
        self._event = None
        self._name = name
        self._offset_reached = False

    @property
    def event(self):
        """Return the next upcoming event."""
        return self._event

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        return await self.session.timetable(
            klasse=self.klasse, start=start_date, end=end_date
        )

    def update(self):
        """Update event data."""
        self.data.update()
        event = copy.deepcopy(self.data.event)
        if event is None:
            self._event = event
            return
        event = calculate_offset(event, OFFSET)
        self._offset_reached = is_offset_reached(event)
        self._event = event


class WebuntisCalendarData:
    """Class to utilize the calendar dav client object to get next event."""

    def __init__(self, session, klasse):
        """Set up how we are going to search the Webuntis calendar."""
        self.session = session
        self.klasse = klasse

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        period_list = await hass.async_add_job(
            self.session.timetable(klasse=self.klasse, start=start_date, end=end_date)
        )
        event_list = []
        for event in period_list:
            data = {
                "code": event.code,
                "type": event.type,
                "subjects": event.subjects,
                "rooms": event.rooms,
                "teachers": event.teachers,
                "start": event.start,
                "end": event.end,
            }

            data["start"] = get_date(data["start"]).isoformat()
            data["end"] = get_date(data["end"]).isoformat()

            event_list.append(event)

        return event_list

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Get the latest data."""
        results = self.session.timetable(
            klasse=self.klasse,
            start=dt.start_of_local_day(),
            end=dt.start_of_local_day() + timedelta(days=1),
        )

        # If no matching event could be found
        if results is []:
            _LOGGER.debug(
                "No matching event found in the %d results for %s",
                len(results),
                self.calendar.name,
            )
            self.event = None
            return

        # Populate the entity attributes with the event values
        event = results[0]
        self.event = {
            "code": event.code,
            "type": event.type,
            "subjects": event.subjects,
            "rooms": event.rooms,
            "teachers": event.teachers,
            "start": event.start,
            "end": event.end,
        }
