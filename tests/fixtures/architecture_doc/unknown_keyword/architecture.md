# Architecture: Example billing system

## Context and scope

Who uses the system and what it talks to.

```mermaid
flowchart TB
    user["Customer [Person]"]
    system["Example billing system [Software system]"]
    ext["Payments [External system]"]
    user --> system
    system --> ext
```

## Constraints

Python 3.10 or newer; no network at lint time.

## Solution strategy

A web front end over one billing service.

## Building blocks

The containers.

```mermaid
flowchart TB
    user["Customer [Person]"]
    ext["Payments [External system]"]
    web["Web UI [TypeScript]"] --> api["Billing API [Python]"]
    user --> web
    api --> ext
```

## Runtime view

How a request flows.

```mermaid
pieChart
    "Dogs" : 386
    "Cats" : 85
```

## Deployment

One container host.

## Cross-cutting concepts

Logging and configuration.

## Decisions

See the decision records.

## Quality requirements

Bills are never lost.

## Risks and technical debt

The payments provider is a single dependency.

## Technology inventory

Every container and its technology.

| Container | Technology |
| --- | --- |
| Web UI | TypeScript |
| Billing API | Python |
