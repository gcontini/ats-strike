# Personal preferences in job selection

Free-form notes read by the JD analyzer. Each section below feeds the field
named in its heading. Rewrite them for yourself -- the model follows what you
write here, so be as specific as you want.

## Salary (field: salary_match)
Compare max_salary (the top of the stated range) with the threshold of the job's band. Do not adjust them for role, seniority or cost of living.

The candidate lives in Turin (Piedmont). If you do not know the distance between Turin and the job location, SEARCH the web. Do not invent distances or places. A location outside Piedmont is most probably far from Turin. Lombardy (outside Milan), Liguria and Emilia-Romagna are in center-north Italy.

Step 1 -- pick ONE band for the job: the first row, top to bottom, that matches.

| Band                  | Applies when                                                      | Threshold            |
|-----------------------|-------------------------------------------------------------------|----------------------|
| Remote                | work_mode is full_remote                                          | max_salary >= 95000  |
| Turin                 | job location within 80 km of Turin                                | max_salary >= 52000  |
| Genoa, Novara         | job location near Genoa or Novara                                 | max_salary >= 60000  |
| Milan                 | job location within 50 km of Milan                                | max_salary >= 70000  |
| Center-north Italy    | any other location in center-north Italy (incl. rest of Lombardy) | max_salary >= 78000  |
| Paris, London, South  | Paris, London or Southern Italy                                   | max_salary >= 85000  |
| Other Europe          | any other European location                                       | max_salary >= 100000 |

Step 2 -- set salary_match:
 A) max_salary is stated: salary_match=true if max_salary >= the band's threshold, false if it is below.
 B) Only a starting salary is stated (e.g. "from 50.000", "50.000+", "base salary 50k") or a single fixed number (e.g. 50k): salary_match=true if that minimum already meets the band's threshold, salary_match=false if it is 20000 or more below the band's threshold, salary_match=null otherwise.
 C) No max_salary stated at all: salary_match=null.

Examples:
 - Job in Turin, "Range retributivo 50-60k€" → max_salary 60000 → band Turin → 60000 >= 52000 → salary_match = true. (Rule A applies)
 - Job in Milan, "Salary starting from 60k€" → max_salary null, starting salary 60000 → band Milan: threshold 70000 → 70000 - 60000 < 20000 → salary_match = null. (Rule B applies)

## Personal preference (field: pers_preference_score)
 - Job location within 70 km of Turin: 1.5 points
 - Hybrid job in Milan: 1 point
 - Fully remote job: 0.5 points
 - The job requires speaking Chinese or dealing with Chinese providers, clients or internal teams: 2 points
 - The company has a strong relationship with China, e.g. it is Chinese owned (you may search the web for this): 1 point
 - Well-known international or Italian company: 0.5 points
 - Innovative job involving AI, or explicitly managing a team: 0.5 points
 - Managing a team or a function (IT Manager): 1 point
 - A broad position that needs both technical and soft skills, where the candidate may face the customer (e.g. presales, team leader) or has to bridge several teams. Do not award it for a purely technical role such as Developer: 1 point

## Should apply (fields: should_apply, should_apply_reason)
Strict rules -- check them in this order; the FIRST one that matches decides. Do NOT evaluate the other rules after the first match.
 1. max_salary is stated (not null) and max_salary <= 45000, or match_percentage <= 60: NO
 2. salary_match is false and (pers_preference_score < 3 or match_percentage <= 65): NO
 3. The location is outside Italy or in Southern Italy: NO
 4. The job_title refers to "Categorie Protette" or the posting is reserved to "Categorie Protette": NO
 5. salary_match is true and match_percentage >= 70 and company_name is one of Contoso, Fabrikam, Tailspin, Globex, Alibaba: CHECK
 7. salary_match is not specified and match_percentage >= 75 and pers_preference_score > 2.5: CHECK
 8. salary_match is true and match_percentage >= 80: YES
 9. salary_match is true and match_percentage >= 70 and pers_preference_score > 2.5: YES
 10. Inconsistencies in the JD (several locations, salary not specified...): apply the judgement rules below.

## Judgement rules -- only for a job that no strict rule above matched:
 - Reject any job unfit for the candidate: the candidate misses a core requirement of the role, e.g. the job_title is "Azure Sysadmin", "Microsoft Systems Engineer" or "experienced IT department manager" (the candidate has no Microsoft experience and has never been a department manager). The missing competence must be core: check the job_title and the first two or three hard skills in the JD. If there is no alignment: NO
 - The JD is missing two or more key pieces of information (e.g. salary, location, work mode), or it is very generic and short (maybe a bait): NO
 - The JD is missing or contradicting ONE piece of information (e.g. salary, location or work mode) but scores very well on all the other dimensions (salary, personal preferences, work mode, fit): CHECK
 - Weigh the job as a whole: if it has strong winning points but falls slightly short on ONE (e.g. good personal preference and salary but a somewhat low match, or a high-match role whose salary is about 5k short): CHECK. If it falls short on TWO or more points (match below ~65, distance...): NO
 - Reject every remaining case: NO
