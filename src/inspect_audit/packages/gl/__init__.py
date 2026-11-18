"""The Generality Labs audit framework: nine dimensions, A-D grades, LaTeX or web report."""

from inspect_audit.package import AuditPackage, Publisher


def _latex() -> Publisher:
    from .latex import publisher

    return publisher()


def _web() -> Publisher:
    from .web import publisher

    return publisher()


def package() -> AuditPackage:
    from .rubric import rubric

    return AuditPackage(
        rubric=rubric(),
        formats={"latex": _latex, "web": _web},
        # `publisher: gl` named the web format before packages existed
        aliases={"gl": "web"},
    )
