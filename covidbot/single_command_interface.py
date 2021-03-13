import logging
import re
import time
from abc import ABC, abstractmethod
from typing import List, Union, Optional, Tuple, Iterable

from covidbot.covid_data import CovidData, Visualization
from covidbot.location_service import LocationService
from covidbot.messenger_interface import MessengerInterface
from covidbot.metrics import RECV_MESSAGE_COUNT, DISCARDED_MESSAGE_COUNT
from covidbot.text_interface import BotResponse
from covidbot.user_manager import UserManager
from covidbot.utils import format_noun, FormattableNoun, format_data_trend, format_float, format_int


class SingleCommandInterface(MessengerInterface, ABC):
    log = logging.getLogger(__name__)
    user_manager: UserManager
    data: CovidData
    viz: Visualization
    location_service: LocationService
    sleep_sec: int
    no_write: bool
    handle_regex = re.compile('@(\w\.@)+')

    rki_name: str = "RKI"
    divi_name: str = "DIVI"
    bmg_name: str = "BMG"

    INFECTIONS_UID = "infections"
    VACCINATIONS_UID = "vaccinations"
    ICU_UID = "icu"

    def __init__(self, user_manager: UserManager, covid_data: CovidData, visualization: Visualization, sleep_sec: int, no_write: bool = False):
        self.data = covid_data
        self.viz = visualization
        self.user_manager = user_manager
        self.location_service = LocationService('resources/germany_rs.geojson')
        self.sleep_sec = sleep_sec
        self.no_write = no_write

    async def send_daily_reports(self) -> None:
        germany = self.data.get_country_data()
        if not germany:
            raise ValueError("Could not find data for Germany")

        # Infections
        infections_uid = self.user_manager.get_user_id(self.INFECTIONS_UID)
        if self.user_manager.get_user(infections_uid).last_update.date() < germany.date:
            tweet_text = f"🦠 Das {self.rki_name} hat für den {germany.date.strftime('%d. %B %Y')} neue Infektionszahlen veröffentlicht.\n\n" \
                         f"Es wurden {format_noun(germany.new_cases, FormattableNoun.INFECTIONS, hashtag='#')} " \
                         f"{format_data_trend(germany.cases_trend)} und " \
                         f"{format_noun(germany.new_deaths, FormattableNoun.DEATHS)} " \
                         f"{format_data_trend(germany.deaths_trend)} in Deutschland gemeldet. Die bundesweite #Inzidenz liegt " \
                         f"bei {format_float(germany.incidence)} {format_data_trend(germany.incidence_trend)}, der " \
                         f"aktuelle R-Wert beträgt {format_float(germany.r_value.r_value_7day)}. #COVID19"
            if self.no_write:
                print(f"Sent message: {tweet_text}")
                self.user_manager.set_last_update(infections_uid, germany.date)
            elif self.write_message(tweet_text, [self.viz.infections_graph(0), self.viz.incidence_graph(0)]):
                self.user_manager.set_last_update(infections_uid, germany.date)
                self.log.info("Tweet was successfully sent")

        # Vaccinations
        vaccinations_uid = self.user_manager.get_user_id(self.VACCINATIONS_UID)
        if self.user_manager.get_user(vaccinations_uid).last_update.date() < germany.vaccinations.date:
            vacc = germany.vaccinations
            tweet_text = f"💉 Das {self.bmg_name} hat die Impfdaten für den {vacc.date.strftime('%d. %B %Y')} veröffentlicht." \
                         f"\n\n{format_float(vacc.partial_rate * 100)}% der Bevölkerung haben mindestens eine #Impfung " \
                         f"erhalten, {format_float(vacc.full_rate * 100)}% sind vollständig geimpft. Insgesamt wurden " \
                         f"{format_int(vacc.vaccinated_partial)} Erstimpfungen und {format_int(vacc.vaccinated_full)} " \
                         f"Zweitimpfungen durchgeführt. #COVID19"

            if self.no_write:
                print(f"Sent message: {tweet_text}")
                self.user_manager.set_last_update(vaccinations_uid, vacc.date)
            elif self.write_message(tweet_text, [self.viz.vaccination_graph(0)]):
                self.user_manager.set_last_update(vaccinations_uid, vacc.date)
                self.log.info("Tweet was successfully sent")

        # Vaccinations
        icu_uid = self.user_manager.get_user_id(self.ICU_UID)
        if self.user_manager.get_user(icu_uid).last_update.date() < germany.icu_data.date:
            icu = germany.icu_data
            tweet_text = f"🏥 Die {self.divi_name} hat Daten über die #Intensivbetten in Deutschland für den " \
                         f"{icu.date.strftime('%d. %B %Y')} gemeldet.\n\n{format_float(icu.percent_occupied())}% " \
                         f"({format_noun(icu.occupied_beds, FormattableNoun.BEDS)}) der " \
                         f"Intensivbetten sind aktuell belegt. " \
                         f"In {format_noun(icu.occupied_covid, FormattableNoun.BEDS)} " \
                         f"({format_float(icu.percent_covid())}%) liegen Patient:innen" \
                         f" mit #COVID19, davon werden {format_int(icu.covid_ventilated)} beatmet. " \
                         f"Insgesamt gibt es {format_noun(icu.total_beds(), FormattableNoun.BEDS)}."

            if self.no_write:
                print(f"Sent message: {tweet_text}")
                self.user_manager.set_last_update(icu_uid, icu.date)
            elif self.write_message(tweet_text):
                self.log.info("Tweet was successfully sent")
                self.user_manager.set_last_update(icu_uid, icu.date)

    def get_infection_tweet(self, district_id: int) -> BotResponse:
        district = self.data.get_district_data(district_id)
        tweet_text = f"🦠 Am {district.date.strftime('%d. %B %Y')} wurden " \
                     f"{format_noun(district.new_cases, FormattableNoun.INFECTIONS, hashtag='#')} " \
                     f"{format_data_trend(district.cases_trend)} und " \
                     f"{format_noun(district.new_deaths, FormattableNoun.DEATHS)} " \
                     f"{format_data_trend(district.deaths_trend)} in {district.name} gemeldet. Die #Inzidenz liegt " \
                     f"bei {format_float(district.incidence)} {format_data_trend(district.incidence_trend)}. #COVID19"
        return BotResponse(tweet_text, [self.viz.incidence_graph(district_id), self.viz.infections_graph(district_id)])

    async def send_message(self, message: str, users: List[Union[str, int]], append_report=False):
        if users:
            self.log.error("Can't tweet to specific users!")
            return

        if len(message) > 240:
            self.log.error("Tweet can't be longer than 240 characters!")
            return

        self.write_message(message)

    @abstractmethod
    def write_message(self, message: str, media_files: Optional[List[str]] = None,
                      reply_id: Optional[int] = None) -> bool:
        pass

    @abstractmethod
    def get_mentions(self) -> Iterable[Tuple[int, str, Optional[str]]]:
        pass

    def run(self) -> None:
        running = True

        while running:
            for chat_id, message, username in self.get_mentions():
                if self.user_manager.is_message_answered(chat_id):
                    continue

                RECV_MESSAGE_COUNT.inc()
                district_id = None
                arguments = message.replace(",", "").replace(".", "").replace("!", "").replace("?", "").strip().split()

                # Manually discard some arguments
                if len(arguments[0]) < 4 and len(arguments) > 3:
                    self.log.warning(f"Do not lookup {arguments}, as it might not be a query but a message")
                    self.user_manager.set_message_answered(chat_id)
                    DISCARDED_MESSAGE_COUNT.inc()
                    continue

                for i in range(min(len(arguments), 3), 0, -1):
                    argument = " ".join(arguments[:i]).strip()
                    districts_query = self.data.search_district_by_name(argument)
                    if districts_query:
                        if len(districts_query) <= 2:
                            district_id = districts_query[0][0]
                            break

                # Check OSM if nothing was found
                if not district_id:
                    results = self.location_service.find_location(" ".join(arguments))
                    if len(results) == 1:
                        district_id = results[0]
                    elif len(results) > 1:
                        results = self.location_service.find_location(" ".join(arguments), restrict_type=True)
                        if len(results) == 1:
                            district_id = results[0]

                # Answer Tweet
                if district_id:
                    if username:
                        username += " "
                    response = self.get_infection_tweet(district_id)
                    message = f"{username}{response.message}"
                    if self.no_write:
                        print(arguments)
                        print(f"Reply to {chat_id}: {message}")
                    else:
                        self.write_message(message, media_files=response.images, reply_id=chat_id)

                self.user_manager.set_message_answered(chat_id)
            time.sleep(self.sleep_sec)
