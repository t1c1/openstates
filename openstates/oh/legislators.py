import re

from billy.scrape import NoDataForPeriod
from billy.scrape.legislators import LegislatorScraper, Legislator

import lxml.html


class OHLegislatorScraper(LegislatorScraper):
    state = 'oh'
    latest_only = True

    def scrape(self, chamber, term):
        if chamber == 'upper':
            self.scrape_senators(chamber, term)
        else:
            self.scrape_reps(chamber, term)

    def scrape_reps(self, chamber, term):
        # There are 99 House districts
        for district in xrange(1, 100):
            rep_url = ('http://www.house.state.oh.us/components/'
                       'com_displaymembers/page.php?district=%d' % district)

            with self.urlopen(rep_url) as page:
                page = lxml.html.fromstring(page)

                ranges = []
                cur = []
                info = page.xpath('//td[@class="info"]/*')
                for r in info:
                    if r.tag == 'strong':
                        ranges.append(cur)
                        cur = []
                    else:
                        cur.append(r)
                ranges.append(cur)

                block = ranges[4][:-1]

                address = ", ".join(
                    [ x.tail.strip() for x in block ])

                phone = page.xpath(
                    "//strong[contains(text(), 'Phone')]")[0].tail

                fax = page.xpath(
                    "//strong[contains(text(), 'Fax')]")[0].tail

                for el in page.xpath('//table[@class="page"]'):
                    rep_link = el.xpath('tr/td/title')[0]
                    full_name = rep_link.text
                    party = full_name[-2]
                    full_name = full_name[0:-3]

                    if full_name == 'Vacant Posit':
                        continue

                    if party == "D":
                        party = "Democratic"
                    elif party == "R":
                        party = "Republican"

                    leg = Legislator(term, chamber, str(district),
                                     full_name, party=party, url=rep_url)
                    leg.add_office('capitol',
                                   'Capitol Office',
                                    address=address,
                                    phone=phone,
                                    fax=fax)  # Yet, no email.

                    leg.add_source(rep_url)

                self.save_legislator(leg)

    def scrape_senators(self, chamber, term):
        url = 'http://www.ohiosenate.gov/directory.html'
        with self.urlopen(url) as page:
            page = lxml.html.fromstring(page)
            page.make_links_absolute(url)

            for el in page.xpath('//table[@class="fullWidth"]/tr/td'):
                sen_link = el.xpath('a[@class="senatorLN"]')[1]
                sen_url = sen_link.get('href')

                full_name = sen_link.text
                full_name = full_name[0:-2]
                if full_name == 'To Be Announced':
                    continue

                district = el.xpath('string(h3)').split()[1]

                party = el.xpath('string(a[@class="senatorLN"]/span)')

                if party == "D":
                    party = "Democratic"
                elif party == "R":
                    party = "Republican"

                office_phone = el.xpath("b[text() = 'Phone']")[0].tail
                office_phone = office_phone.strip(' :')

                office = ", ".join([x.strip() for x in \
                                    el.xpath("./text()")[2:-1]])

                photo_url = el.xpath("a/img")[0].attrib['src']
                email = el.xpath('.//span[@class="tan"]/text()')[1]

                leg = Legislator(term, chamber, district, full_name,
                                 party=party, photo_url=photo_url, url=sen_url)

                leg.add_office('capitol',
                               'Capitol Office',
                               address=office,
                               phone=office_phone)

                leg.add_source(url)

                self.save_legislator(leg)
