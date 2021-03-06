import re
from datetime import datetime
from collections import defaultdict

from .utils import chamber_name, parse_ftp_listing
from billy.scrape.bills import BillScraper, Bill
from billy.scrape.votes import VoteScraper, Vote

import lxml.html

class NVBillScraper(BillScraper):
    state = 'nv'

    _classifiers = (
        ('Approved by the Governor', 'governor:signed'),
        ('Bill read. Veto not sustained', 'bill:veto_override:passed'),
        ('Bill read. Veto sustained', 'bill:veto_override:failed'),
        ('Enrolled and delivered to Governor', 'governor:received'),
        ('From committee: .+? adopted', 'committee:passed'),
        ('From committee: .+? pass', 'committee:passed'),
        ('Prefiled. Referred', ['bill:introduced', 'committee:referred']),
        ('Read first time. Referred', ['bill:reading:1', 'committee:referred']),
        ('Read first time.', 'bill:reading:1'),
        ('Read second time.', 'bill:reading:2'),
        ('Read third time. Lost', ['bill:failed', 'bill:reading:3']),
        ('Read third time. Passed', ['bill:passed', 'bill:reading:3']),
        ('Read third time.', 'bill:reading:3'),
        ('Rereferred', 'committee:referred'),
        ('Resolution read and adopted', 'bill:passed'),
        ('Vetoed by the Governor', 'governor:vetoed')
    )

    def scrape(self, chamber, session):
        if 'Special' in session:
            year = session[0:4]
        elif int(session) >= 71:
            year = ((int(session) - 71) * 2) + 2001
        else:
            raise NoDataForPeriod(session)

        sessionsuffix = 'th'
        if str(session)[-1] == '1':
            sessionsuffix = 'st'
        elif str(session)[-1] == '2':
            sessionsuffix = 'nd'
        elif str(session)[-1] == '3':
            sessionsuffix = 'rd'

        self.subject_mapping = defaultdict(list)

        if 'Special' in session:
            insert = session[-2:] + sessionsuffix + str(year) + "Special"
        else:
            insert = str(session) + sessionsuffix + str(year)
            self.scrape_subjects(insert, session, year)

        if chamber == 'upper':
            self.scrape_senate_bills(chamber, insert, session, year)
        elif chamber == 'lower':
            self.scrape_assem_bills(chamber, insert, session, year)

    def scrape_subjects(self, insert, session, year):
        url = 'http://www.leg.state.nv.us/Session/%s/Reports/TablesAndIndex/%s_%s-index.html' % (insert, year, session)

        with self.urlopen(url) as html:
            doc = lxml.html.fromstring(html)

            # first, a bit about this page:
            # Level0 are the bolded titles
            # Level1,2,3,4 are detailed titles, contain links to bills
            # all links under a Level0 we can consider categorized by it
            # there are random newlines *everywhere* that should get replaced

            subject = None

            for p in doc.xpath('//p'):
                if p.get('class') == 'Level0':
                    subject = p.text_content().replace('\r\n', ' ')
                else:
                    if subject:
                        for a in p.xpath('.//a'):
                            bill_id = (a.text.replace('\r\n', '') if a.text
                                       else None)
                            self.subject_mapping[bill_id].append(subject)

    def scrape_senate_bills(self, chamber, insert, session, year):
        doc_type = {2: 'bill', 4: 'resolution', 7: 'concurrent resolution',
                    8: 'joint resolution'}

        for docnum, bill_type in doc_type.iteritems():
            parentpage_url = 'http://www.leg.state.nv.us/Session/%s/Reports/HistListBills.cfm?DoctypeID=%s' % (insert, docnum)
            links = self.scrape_links(parentpage_url)
            count = 0
            for link in links:
                count = count + 1
                page_path = 'http://www.leg.state.nv.us/Session/%s/Reports/%s' % (insert, link)

                with self.urlopen(page_path) as page:
                    page = page.replace(u"\xa0", " ")
                    root = lxml.html.fromstring(page)

                    bill_id = root.xpath('string(/html/body/div[@id="content"]/table[1]/tr[1]/td[1]/font)')
                    title = root.xpath('string(/html/body/div[@id="content"]/table[1]/tr[5]/td)')

                    bill = Bill(session, chamber, bill_id, title,
                                type=bill_type)
                    bill['subjects'] = self.subject_mapping[bill_id]

                    bill_text = root.xpath("string(/html/body/div[@id='content']/table[6]/tr/td[2]/a/@href)")
                    text_url = "http://www.leg.state.nv.us" + bill_text
                    bill.add_version("Bill Text", text_url)

                    primary, secondary = self.scrape_sponsors(page)

                    for leg in primary:
                        bill.add_sponsor('primary', leg)
                    for leg in secondary:
                        bill.add_sponsor('cosponsor', leg)


                    minutes_count = 2
                    for mr in root.xpath('//table[4]/tr/td[3]/a'):
                        minutes =  mr.xpath("string(@href)")
                        minutes_url = "http://www.leg.state.nv.us" + minutes
                        minutes_date_path = "string(//table[4]/tr[%s]/td[2])" % minutes_count
                        minutes_date = mr.xpath(minutes_date_path).split()
                        minutes_date = minutes_date[0] + minutes_date[1] + minutes_date[2] + " Agenda"
                        bill.add_document(minutes_date, minutes_url)
                        minutes_count = minutes_count + 1

                    self.scrape_actions(root, bill, "upper")
                    self.scrape_votes(page, bill, insert, year)
                    bill.add_source(page_path)
                    self.save_bill(bill)



    def scrape_assem_bills(self, chamber, insert, session, year):

        doc_type = {1: 'bill', 3: 'resolution', 5: 'concurrent resolution',
                    6: 'joint resolution'}
        for docnum, bill_type in doc_type.iteritems():
            parentpage_url = 'http://www.leg.state.nv.us/Session/%s/Reports/HistListBills.cfm?DoctypeID=%s' % (insert, docnum)
            links = self.scrape_links(parentpage_url)
            count = 0
            for link in links:
                count = count + 1
                page_path = 'http://www.leg.state.nv.us/Session/%s/Reports/%s' % (insert, link)
                with self.urlopen(page_path) as page:
                    page = page.replace(u"\xa0", " ")
                    root = lxml.html.fromstring(page)

                    bill_id = root.xpath('string(/html/body/div[@id="content"]/table[1]/tr[1]/td[1]/font)')
                    title = root.xpath('string(/html/body/div[@id="content"]/table[1]/tr[5]/td)')

                    bill = Bill(session, chamber, bill_id, title,
                                type=bill_type)
                    bill['subjects'] = self.subject_mapping[bill_id]
                    bill_text = root.xpath("string(/html/body/div[@id='content']/table[6]/tr/td[2]/a/@href)")
                    text_url = "http://www.leg.state.nv.us" + bill_text
                    bill.add_version("Bill Text", text_url)

                    primary, secondary = self.scrape_sponsors(page)

                    for leg in primary:
                        bill.add_sponsor('primary', leg)
                    for leg in secondary:
                        bill.add_sponsor('cosponsor', leg)

                    minutes_count = 2
                    for mr in root.xpath('//table[4]/tr/td[3]/a'):
                        minutes =  mr.xpath("string(@href)")
                        minutes_url = "http://www.leg.state.nv.us" + minutes
                        minutes_date_path = "string(//table[4]/tr[%s]/td[2])" % minutes_count
                        minutes_date = mr.xpath(minutes_date_path).split()
                        minutes_date = minutes_date[0] + minutes_date[1] + minutes_date[2] + " Minutes"
                        bill.add_document(minutes_date, minutes_url)
                        minutes_count = minutes_count + 1


                    self.scrape_actions(root, bill, "lower")
                    self.scrape_votes(page, bill, insert, year)
                    bill.add_source(page_path)
                    self.save_bill(bill)

    def scrape_links(self, url):
        links = []

        with self.urlopen(url) as page:
            root = lxml.html.fromstring(page)
            path = '/html/body/div[@id="ScrollMe"]/table/tr[1]/td[1]/a'
            for mr in root.xpath(path):
                if '*' not in mr.text:
                    web_end = mr.xpath('string(@href)')
                    links.append(web_end)
        return links


    def scrape_sponsors(self, page):
        primary = []
        sponsors = []
        doc = lxml.html.fromstring(page)
        for b in doc.xpath('//div[@id="content"]/table[1]/tr[4]/td/b'):
            name = b.text.strip()
            # add these as sponsors (excluding junk text)
            if name not in ('By:', 'Bolded'):
                primary.append(name)

        # tail of last b has remaining sponsors
        for name in b.tail.split(', '):
            if name.strip():
                sponsors.append(name.strip())

        return primary, sponsors

    def scrape_actions(self, root, bill, actor):
        path = '/html/body/div[@id="content"]/table/tr/td/p[1]'
        for mr in root.xpath(path):
            date = mr.text_content().strip()
            date = date.split()[0] + " " + date.split()[1] + " " + date.split()[2]
            date = datetime.strptime(date, "%b %d, %Y")
            for el in mr.xpath('../../following-sibling::tr[1]/td/ul/li'):
                action = el.text_content().strip()

                # skip blank actions
                if not action:
                    continue

                # catch chamber changes
                if action.startswith('In Assembly'):
                    actor = 'lower'
                elif action.startswith('In Senate'):
                    actor = 'upper'
                elif 'Governor' in action:
                    actor = 'executive'

                action_type = 'other'
                for pattern, atype in self._classifiers:
                    if re.match(pattern, action):
                        action_type = atype
                        break

                bill.add_action(actor, action, date, type=action_type)

    def scrape_votes(self, bill_page, bill, insert, year):
        root = lxml.html.fromstring(bill_page)
        for link in root.xpath('//a[contains(text(), "Passage")]'):
            motion = link.text
            if 'Assembly' in motion:
                chamber = 'lower'
            else:
                chamber = 'upper'
            vote_url = 'http://www.leg.state.nv.us/Session/%s/Reports/%s' % (
                insert, link.get('href'))
            bill.add_source(vote_url)
            with self.urlopen(vote_url) as page:
                page = page.replace(u"\xa0", " ")
                root = lxml.html.fromstring(page)

                date = root.xpath('//h1/text()')[-1].strip()
                date = datetime.strptime(date, "%B %d, %Y at %H:%M %p")
                top_block_text = root.xpath('//div[@align="center"]')[0].text_content()
                yes_count = int(re.findall("(\d+) Yea", top_block_text)[0])
                no_count = int(re.findall("(\d+) Nay", top_block_text)[0])
                excused = int(re.findall("(\d+) Excused", top_block_text)[0])
                not_voting = int(re.findall("(\d+) Not Voting", top_block_text)[0])
                absent = int(re.findall("(\d+) Absent", top_block_text)[0])
                other_count = excused + not_voting + absent
                passed = yes_count > no_count

                vote = Vote(chamber, date, motion, passed, yes_count, no_count,
                            other_count, not_voting=not_voting, absent=absent)

                for el in root.xpath('//table[2]/tr'):
                    tds = el.xpath('td')
                    name = tds[1].text_content().strip()
                    vote_result = tds[2].text_content().strip()

                    if vote_result == 'Yea':
                        vote.yes(name)
                    elif vote_result == 'Nay':
                        vote.no(name)
                    else:
                        vote.other(name)
                bill.add_vote(vote)
