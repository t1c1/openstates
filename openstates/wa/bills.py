import re
import datetime
from collections import defaultdict

from .utils import xpath
from billy.scrape.bills import BillScraper, Bill
from billy.scrape.votes import Vote

import lxml.etree
import lxml.html
import feedparser

class WABillScraper(BillScraper):
    state = 'wa'

    _base_url = 'http://wslwebservices.leg.wa.gov/legislationservice.asmx'

    _subjects = defaultdict(list)

    def build_subject_mapping(self, year):
        url = 'http://apps.leg.wa.gov/billsbytopic/Results.aspx?year=%s' % year
        with self.urlopen(url) as html:
            doc = lxml.html.fromstring(html)
            doc.make_links_absolute('http://apps.leg.wa.gov/billsbytopic/')
            for link in doc.xpath('//a[contains(@href, "ResultsRss")]/@href'):
                subject = link.rsplit('=',1)[-1]
                rss = feedparser.parse(self.urlopen(link.replace(' ', '%20')))
                for e in rss['entries']:
                    match = re.match('\w\w \d{4}', e['title'])
                    if match:
                        self._subjects[match.group()].append(subject)


    def scrape(self, chamber, session):
        bill_id_list = []
        year = int(session[0:4])

        # first go through API response and get bill list
        for y in (year, year+1):
            self.build_subject_mapping(y)
            url = "%s/GetLegislationByYear?year=%s" % (self._base_url, y)

            with self.urlopen(url) as page:
                page = lxml.etree.fromstring(page.bytes)

                for leg_info in xpath(page, "//wa:LegislationInfo"):
                    bill_id = xpath(leg_info, "string(wa:BillId)")
                    bill_num = int(bill_id.split()[1])

                    # Skip gubernatorial appointments
                    if bill_num >= 9000:
                        continue

                    # Senate bills are numbered starting at 5000,
                    # House at 1000
                    if bill_num > 5000:
                        bill_chamber = 'upper'
                    else:
                        bill_chamber = 'lower'

                    if bill_chamber != chamber:
                        continue

                    # normalize bill_id
                    bill_id_norm = re.findall('(?:S|H)(?:B|CR|JM|JR|R) \d+',
                                              bill_id)
                    if not bill_id_norm:
                        self.warning("illegal bill_id %s" % bill_id)
                        continue

                    bill_id_list.append(bill_id_norm[0])

        # de-dup bill_id
        for bill_id in list(set(bill_id_list)):
            bill = self.scrape_bill(chamber, session, bill_id)
            bill['subjects'] = self._subjects[bill_id]
            self.save_bill(bill)

    def scrape_bill(self, chamber, session, bill_id):
        biennium = "%s-%s" % (session[0:4], session[7:9])
        bill_num = bill_id.split()[1]

        url = ("%s/GetLegislation?biennium=%s&billNumber"
               "=%s" % (self._base_url, biennium, bill_num))

        with self.urlopen(url) as page:
            page = lxml.etree.fromstring(page.bytes)
            page = xpath(page, "//wa:Legislation")[0]

            title = xpath(page, "string(wa:LongDescription)")

            bill_type = xpath(
                page,
                "string(wa:ShortLegislationType/wa:LongLegislationType)")
            bill_type = bill_type.lower()

            if bill_type == 'gubernatorial appointment':
                return

            bill = Bill(session, chamber, bill_id, title,
                        type=[bill_type])

            chamber_name = {'lower': 'House', 'upper': 'Senate'}[chamber]
            version_url = ("http://www.leg.wa.gov/pub/billinfo/2011-12/"
                           "Htm/Bills/%s %ss/%s.htm" % (chamber_name,
                                                        bill_type.title(),
                                                        bill_num))
            bill.add_version(bill_id, version_url)

            fake_source = ("http://apps.leg.wa.gov/billinfo/"
                           "summary.aspx?bill=%s&year=%s" % (
                               bill_num, session[0:4]))
            bill.add_source(fake_source)

            self.scrape_sponsors(bill)
            self.scrape_actions(bill)
            self.scrape_votes(bill)

            return bill

    def scrape_sponsors(self, bill):
        bill_id = bill['bill_id'].replace(' ', '%20')
        session = bill['session']
        biennium = "%s-%s" % (session[0:4], session[7:9])

        url = "%s/GetSponsors?biennium=%s&billId=%s" % (
            self._base_url, biennium, bill_id)

        with self.urlopen(url) as page:
            page = lxml.etree.fromstring(page.bytes)

            for sponsor in xpath(page, "//wa:Sponsor/wa:Name"):
                bill.add_sponsor('primary', sponsor.text)

    def scrape_actions(self, bill):
        bill_id = bill['bill_id'].replace(' ', '%20')
        session = bill['session']
        biennium = "%s-%s" % (session[0:4], session[7:9])
        begin_date = "%s-01-10T00:00:00" % session[0:4]
        end_date = "%d-01-10T00:00:00" % (int(session[5:9]) + 1)

        chamber = bill['chamber']

        url = ("%s/GetLegislativeStatusChangesByBillId?billId=%s&"
               "biennium=%s&beginDate=%s&endDate=%s" % (self._base_url,
                                                        bill_id,
                                                        biennium,
                                                        begin_date,
                                                        end_date))
        with self.urlopen(url) as page:
            page = lxml.etree.fromstring(page.bytes)

            for status in xpath(page, "//wa:LegislativeStatus"):
                action = xpath(status, "string(wa:HistoryLine)").strip()
                date = xpath(status, "string(wa:ActionDate)").split("T")[0]
                date = datetime.datetime.strptime(date, "%Y-%m-%d").date()

                atype = []

                if (action.startswith('Third reading, passed') or
                    action.startswith('Third reading, adopted')):

                    chamber = {'upper': 'lower', 'lower': 'upper'}[chamber]
                    atype.append('bill:passed')
                    atype.append('bill:reading:3')

                actor = chamber

                if (action.startswith('First reading') or
                    action.startswith('Read first time')):

                    atype.append('bill:introduced')
                    atype.append('bill:reading:1')
                elif action.startswith('Introduced'):
                    atype.append('bill:introduced')
                elif action.startswith('Governor signed'):
                    actor = 'executive'
                    atype.append('governor:signed')
                elif action == 'Adopted.':
                    atype.append('bill:passed')

                if 'Floor amendment(s) adopted' in action:
                    atype.append('amendment:passed')

                if 'referred' in action.lower():
                    atype.append('committee:referred')

                bill.add_action(actor, action, date, type=atype)

    def scrape_votes(self, bill):
        session = bill['session']
        biennium = "%s-%s" % (session[0:4], session[7:9])
        bill_num = bill['bill_id'].split()[1]

        url = ("http://wslwebservices.leg.wa.gov/legislationservice.asmx/"
               "GetRollCalls?billNumber=%s&biennium=%s" % (
                   bill_num, biennium))
        with self.urlopen(url) as page:
            page = lxml.etree.fromstring(page.bytes)

            for rc in xpath(page, "//wa:RollCall"):
                motion = xpath(rc, "string(wa:Motion)")

                date = xpath(rc, "string(wa:VoteDate)").split("T")[0]
                date = datetime.datetime.strptime(date, "%Y-%m-%d").date()

                yes_count = int(xpath(rc, "string(wa:YeaVotes/wa:Count)"))
                no_count = int(xpath(rc, "string(wa:NayVotes/wa:Count)"))
                abs_count = int(
                    xpath(rc, "string(wa:AbsentVotes/wa:Count)"))
                ex_count = int(
                    xpath(rc, "string(wa:ExcusedVotes/wa:Count)"))

                other_count = abs_count + ex_count

                agency = xpath(rc, "string(wa:Agency)")
                chamber = {'House': 'lower', 'Senate': 'upper'}[agency]

                vote = Vote(chamber, date, motion,
                            yes_count > (no_count + other_count),
                            yes_count, no_count, other_count)

                for sv in xpath(rc, "wa:Votes/wa:Vote"):
                    name = xpath(sv, "string(wa:Name)")
                    vtype = xpath(sv, "string(wa:VOte)")

                    if vtype == 'Yea':
                        vote.yes(name)
                    elif vtype == 'Nay':
                        vote.no(name)
                    else:
                        vote.other(name)

                bill.add_vote(vote)
