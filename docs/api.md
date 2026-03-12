# TUMonline REST API — Reverse-Engineering Notes

This document captures everything discovered about the TUMonline
(CAMPUSonline) public REST API used by the Angular frontend at
`campus.tum.de`. None of this is officially documented; it was
reverse-engineered by analysing the Angular SPA's webpack bundle
chunks and testing against the live server.

---

## Base URLs

| Purpose       | URL                                                                    |
| ------------- | ---------------------------------------------------------------------- |
| Course list   | `https://campus.tum.de/tumonline/ee/rest/slc.tm.cp/student/courses`    |
| Course detail | `…/courses/{courseId}`                                                 |
| Course groups | `…/courseGroups/firstGroups/{courseId}`                                |
| Semesters     | `https://campus.tum.de/tumonline/ee/rest/slc.lib.tm/semesters/student` |

All endpoints are **public** (no authentication token required).
Responses are JSON; send `Accept: application/json`.

---

## Semesters Endpoint

```
GET /ee/rest/slc.lib.tm/semesters/student
```

Returns **all semesters** known to TUMonline (134 as of March 2026),
wrapped in a JSON object:

```json
{
  "semesters": [
    {
      "id": 206,
      "key": "26S",
      "semesterType": "S",
      "isSelected": true,
      "startOfAcademicSemester": { "value": "2026-04-01" },
      "endOfAcademicSemester":   { "value": "2026-09-30" },
      "semesterDesignation": { "translations": { "translation": [...] } }
    },
    ...
  ]
}
```

- Ordered **most-recent first**.
- `isSelected: true` marks the server's "current" semester.
- `id` is a numeric term identifier used for filtering (see below).
- `key` is the short semester code (`"26S"`, `"25W"`, `"25S"`, …).

### Known semester ids

| id  | key | Human-readable |
| --- | --- | -------------- |
| 206 | 26S | Summer 2026    |
| 205 | 25W | Winter 2025/26 |
| 204 | 25S | Summer 2025    |
| 203 | 24W | Winter 2024/25 |

---

## Course List Endpoint

```
GET /ee/rest/slc.tm.cp/student/courses?$skip=0&$top=100
```

### Pagination

| Param   | Type | Description             |
| ------- | ---- | ----------------------- |
| `$skip` | int  | Number of items to skip |
| `$top`  | int  | Page size (max ~100)    |

### Response shape

```json
{
  "courses": [ ... ],
  "totalCount": 6551,
  "links": [ ... ]
}
```

> **Important:** Earlier documentation (and some API versions) used
> `resource` / `totalEntities` as keys. The current public endpoint
> uses `courses` / `totalCount`.

### Filtering with `$filter`

The Angular frontend uses a **matrix-parameter style** filter format
that the server recognises. This was the hardest part to figure out.

#### Format

```
?$filter=field1-operator1=value1;field2-operator2=value2
```

- **Field–operator separator:** `-` (hyphen).
- **Key–value separator:** `=` (equals).
- **Filter-pair separator:** `;` (semicolon).

This is effectively a semicolon-delimited list of `key=value` pairs
where each key is `field-operator`.

#### Discovered filter fields

| Filter expression        | Effect                                  |
| ------------------------ | --------------------------------------- |
| `termId-eq=204`          | Only courses from semester id 204 (25S) |
| `courseTypeKey-eq=VO`    | Only lectures                           |
| `filterTerm-like=physik` | Server-side text search                 |

Filters can be **combined** with `;`:

```
?$filter=termId-eq=204;courseTypeKey-eq=VO
→ Only lectures from Summer 2025
```

#### How it was discovered

The Angular SPA's JavaScript bundles contain a `mergeFilter()` method
and a `serializeMatrixParamValue()` serializer. Key constants from the
bundle:

```javascript
FILTER_PARAM_NAME = "$filter";
FILTER_OPERATOR_SEPARATOR = "-"; // between field and operator
MATRIX_PARAM_SEPARATOR = ";"; // between filter pairs
PARAM_VALUE_SEPARATOR = "="; // between key and value
ARRAY_PARAM_SEPARATOR = ","; // for multi-value fields
```

`mergeFilter()` stores filter entries as `{ "field-op": value }` objects
in the URL query. The serializer iterates sorted keys and joins them
with `;`.

### What does NOT work

| Attempt                           | Result                       |
| --------------------------------- | ---------------------------- |
| `?semesterKey=25S`                | **Silently ignored** (no-op) |
| `?semesterId=204`                 | **Silently ignored** (no-op) |
| `?$filter=termId eq 204` (OData)  | `400 Bad Request`            |
| `?$filter=termId-eq-204` (no `=`) | `200` but filter **ignored** |

The crucial difference between the broken `termId-eq-204` and the
working `termId-eq=204` is the `=` sign: the server parses
`;`-separated `key=value` pairs, so without the `=` the whole thing is
treated as a key with no value and discarded.

### HTTPx gotcha

Python's `httpx` URL-encodes query parameters, so:

```python
# BROKEN — httpx encodes $ → %24, server doesn't recognise it
client.get(url, params={"$filter": "termId-eq=204"})

# WORKS — build the URL string manually
client.get(f"{COURSES_URL}?$filter=termId-eq=204&$skip=0&$top=100")
```

---

## Course Detail Endpoint

```
GET /ee/rest/slc.tm.cp/student/courses/{courseId}
```

Works for **any** course id from **any** semester (not just the
"selected" one).

### Response structure

```json
{
  "resource": [
    {
      "content": {
        "cpCourseDetailDto": {
          "cpCourseDto": {
            "courseTitle": { ... },
            "semesterDto": { "key": "25W" },
            "courseTypeDto": { "key": "VO" },
            "organisationResponsibleDto": { ... },
            "courseNumber": { "courseNumber": "IN2346" },
            "lectureships": [ ... ],
            "courseNormConfigs": [ ... ]
          },
          "cpCourseDescriptionDto": {
            "courseContent":    { "translations": ... },
            "courseObjective":  { "translations": ... },
            "previousKnowledge": { "translations": ... },
            "additionalInformation": {
              "recommendedLiterature": { "translations": ... }
            }
          },
          "sameCourseDtos": [ ... ]
        }
      }
    }
  ]
}
```

> Note: `sameCourseDtos` lists the same course offered in other
> semesters — useful for cross-referencing, but not currently used.

---

## Course Groups / Appointments Endpoint

```
GET /ee/rest/slc.tm.cp/student/courseGroups/firstGroups/{courseId}
```

Discovered via Playwright network tracing of the Angular SPA.
Returns course groups with **appointment and room data** — the key
endpoint missing from earlier reverse-engineering attempts.

### Response structure

```json
{
  "courseGroupDtos": [
    {
      "id": 1334572,
      "name": "Standardgruppe",
      "courseId": 950935353,
      "standardgroupFlag": "J",
      "appointmentDtos": [
        {
          "id": 892686492,
          "weekday": { "key": "Di.", "langDataType": { "value": "Dienstag" } },
          "timestampFrom": { "value": "2026-04-14T10:00:00" },
          "timestampTo":   { "value": "2026-04-14T12:00:00" },
          "resourceId": 29092,
          "resourceName": "Hörsaal im Galileo nur Mo-Do 7-19 Uhr (8120.EG.001)",
          "resourceUrl": "/app/desktop/#/pl/ui/$ctx/ris.einzelRaum?raumKey=67759",
          "appointmentStatusType": "CONFIRMED",
          "appointmentSeriesId": 667919
        }
      ],
      "lectureshipDtos": [ ... ],
      "appointmentSeriesDtos": [ ... ]
    }
  ],
  "links": [ ... ],
  "totalCount": 1
}
```

### Room / building codes

The `resourceName` field contains a **TUM room code** in parentheses,
e.g. `(8120.EG.001)`. The 4-digit prefix is the building code.

Building codes are resolved to campus labels dynamically via the
**TUM NavigaTUM API** (`nav.tum.de/api/search?q=<building_code>`).
Room search results include a `subtext` field with the campus label
(e.g. `"garching, Mathe/Info (MI)"`). Results are cached in a
`building_campuses` SQLite table so the API is only queried for
newly seen building codes.

Rough prefix patterns (for reference only — NavigaTUM is authoritative):

| Prefix | Typical campus          |
| ------ | ----------------------- |
| 0xxx   | stammgelände            |
| 1xxx   | stammgelände            |
| 2xxx   | marsstraße / karlstraße |
| 4xxx   | weihenstephan           |
| 5xxx   | garching                |
| 8xxx   | garching (newer)        |

Some resources lack building codes:

- `"Online: Videokonferenz"` — no room code
- `"Externer Ort (siehe Anmerkung)"` — external venue
- `"Ort/Zeit nicht bekannt"` — TBA

### Coverage

~61% of courses have at least one appointment with room data.
Online-only and TBA courses have no building codes (~39% have no
campus assigned).

---

## Data NOT available in the public API

### Authenticated public REST API

TUM also operates a separate **public REST API** at
`https://campus.tum.de/tumonline/co/co-*/api/*` with OpenAPI specs.
This includes detailed appointment, room, and facilities endpoints,
but all return **401 Unauthorized** without a registered Client-ID.

---

## Typical course counts per semester

| Semester | Courses |
| -------- | ------- |
| 26S      | ~4 600  |
| 25W      | ~6 550  |
| 25S      | ~6 370  |
| 24W      | ~6 520  |

Summer semesters tend to have slightly fewer courses. The most recent
future semester (26S) may still be growing as departments publish.
