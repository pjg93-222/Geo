"""Synthetic display names for demo mode (stdlib only).

Demo mode is display-only pseudonymisation: real scores, synthetic names. The lists below are written into the code and
are never drawn from the contacts layer. A person's first name depends on the person id, and the surname depends on the
household code, so the members of one household share a surname.

    first = FIRST[HMAC(key, "f" + person_id) mod len(FIRST)]
    last  = LAST[HMAC(key, "l" + household_code) mod len(LAST)]

The key is stored in scores.sqlite3 metadata (it is not secret and is not the street salt): the names carry no
information about any real person.
"""
import hashlib
import hmac

FIRST = tuple(dict.fromkeys("""
Avery Blair Cameron Dakota Eden Finley Gray Harper Indigo Jordan Kai Lane Marlow Nova Oakley Parker Quinn Reese Sage
Taylor Uma Vale Wren Xan Yael Zion Aria Beck Cleo Dale Elio Fern Gale Hollis Ivy Jules Kit Lark Milo Nico Onyx Pax
Remy Sky Tate Una Vesper Wynn Yuri Zane Alder Briar Cove Drew Ember Flint Glen Haven Iris Juno Knox Laurel Maren
Noel Orion Piper Rowan Sloane Tegan Ulla Vance Wesley Yara Zora Arden Bryn Corin Devon Esme Fallon Greer Hale Imani
Joss Kendall Landry Mika Nell Oren Pryor Quincy Rory Shiloh Teagan Urban Vida Wells Yosef Zeke Ainsley Brook Callum
Dara Emory Frances Gideon Hattie Isaac Jonah Kira Leif Maya Noor Otis Penny Rhea Silas Thea Ulric Violet Wade Ximena
Yasmin Zander Abel Bianca Caleb Dina Eli Flora Gus Hana Ian June Karl Lena Mateo Nadia Owen Priya Raul Sofia Tobias
Ursula Victor Willa Xavier Yvette Zach Adele Boris Chloe Darius Elena Felix Gwen Hugo Ines Joel Kayla Liam Mona
Nolan Opal Pablo Rita Simon Tessa Umar Vera Walt Xena Yves Zelda Alma Brent Carla Dean Elsa Fabio Gina Hank Ida
Jack Kate Luis Mara Neil Ora Pedro Ruth Seth Tara Uri Vince Wanda Yoko Zane Amara Bruno Celia Diego Edith Floyd
Greta Heath Ilsa Jasper Kenji Luna Marco Nina Oscar Petra Rafael Selma Trent Uriel Vivian Warren Ximeno Yolanda
Zoltan Alana Barrett Coral Declan Estelle Forrest Giselle Heidi Ignacio Jolene Kellan Lucia Marcel Noemi Ophelia
Preston Rosalie Stellan Thalia Ulises Valerie Winston Xiomara Yannick Zinnia Anselm Beatrix Cyrus Delphine Everett
Fiona Gareth Helena Ismael Jocelyn Kieran Liana Matteo Nerys Octavio Paloma Raine Sabine Tomas Urielle Vaughn
Wilder Xaviera Yardley Zephyr Ansel Bettina Corbin Dorian Elara Fenwick Galen Hester Idris Jovie Kestrel Lysander
Marisa Nolie Odessa Perrin Quillon Rosario Saskia Tamsin Ulrika Verity Weston Ysolde Zinaida Alaric Bellamy Calla
Dashiell Elowen Finnian Gwyneth Horatio Isolde Jericho Katya Leopold Mirabel Nikolai Orla Percival Rafferty
""".split()))

LAST = tuple(dict.fromkeys("""
Abbott Ainsworth Alder Ames Ashby Atwood Avery Bailey Baxter Beckett Bellamy Bennett Birch Blackwell Bloom Boyd
Bradley Brandt Briggs Brooks Burke Byrne Calloway Carver Chandler Chase Clayton Cole Collier Conway Cooper Crane
Crawford Dalton Davenport Dawson Delaney Dexter Donovan Drake Dunbar Easton Eaton Edison Ellis Emerson Everett
Fairbanks Farley Fenton Ferris Finch Fleming Foster Fowler Gallagher Gardner Garrison Gibson Gilbert Goodwin
Granger Grayson Hadley Hale Hamilton Hanson Harlan Harper Hartley Hawkins Hayes Hendrix Holbrook Holloway Hudson
Hunter Hutchins Ingram Irving Jackson Jarvis Jennings Keaton Kendrick Kerr Kingsley Kirby Lambert Langley Larkin
Lawson Leland Lennox Lindsey Locke Lowell Lyman Mackenzie Maddox Mallory Marlowe Marsh Mason Maxwell McCall
Mercer Merritt Monroe Morrison Mulligan Nash Navarro Nelson Newell Norris Oakes Oliver Osborne Owens Palmer
Parrish Paxton Pearce Pemberton Perry Pierce Pollard Prescott Quinlan Radcliffe Raleigh Ramsey Rawlins Reeves
Rhodes Richter Riley Roberts Rowland Rutledge Salinger Sanford Sawyer Sheridan Shelby Sinclair Slater Spencer
Stanton Sterling Stockton Sutton Talbot Tanner Thatcher Thorne Tillman Tobin Townsend Trask Turner Tyler Upton
Vance Vaughn Wakefield Walden Waller Walsh Warner Watkins Weaver Webster Wellington Wheeler Whitaker Whitfield
Wilder Winslow Winters Woodard Wright Yates York Zimmer Ackerman Alvarado Barlow Bishop Bowman Burgess Cabot
Caldwell Cantrell Carlisle Castillo Cavanaugh Clemons Coburn Colby Cromwell Dillard Dunham Durand Eastman Elkins
Emery Farrow Fisk Flanagan Forbes Frazier Gaines Gentry Gillespie Glover Gower Greer Griffith Hammond Hargrove
Haskell Heath Hobbs Hollis Hopper Houston Ives Jessup Judd Kemp Kimball Knowles Lacey Lathrop Leighton Lockhart
Lund Mabry Mayfield McKay Meadows Mills Monahan Moody Morrow Nixon Noble Norwood Odom Orr Overton Pace Pruitt
Quigley Redfield Reyes Rollins Rowe Sandoval Santos Savage Seymour Shepard Snell Stafford Strand Sumner Swanson
Tate Teague Thorpe Toll Truitt Underhill Valentine Vega Wade Wiley Wolfe Yarbrough Zane Abernathy Bancroft
Blanchard Cartwright Dunmore Fairchild Garrick Hollander Kensington Lockwood Mortimer Northcott Penhallow Quayle
Ravenscroft Stanhope Thackeray Wetherby Ashworth Brightwell Calder Dewhurst Ellsworth Farnsworth Gladstone Hartwell
""".split()))

assert len(FIRST) == len(set(FIRST)) >= 300, (len(FIRST), len(set(FIRST)))
assert len(LAST) == len(set(LAST)) >= 300, (len(LAST), len(set(LAST)))


def _index(key, tag, value, size):
    digest = hmac.new(key, (tag + str(value)).encode('utf-8'), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], 'big') % size


def first_name(key, person_id):
    return FIRST[_index(key, 'f', person_id, len(FIRST))]


def last_name(key, household_code):
    return LAST[_index(key, 'l', household_code, len(LAST))]


def demo_name(key, person_id, household_code):
    return first_name(key, person_id) + ' ' + last_name(key, household_code)
