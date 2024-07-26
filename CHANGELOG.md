## v1.0.2

* Fix issue where Django's HttpResponse object doesn't accept the headers keyword argument. Headers are now applied to the response object directly.

## v1.0.1

* Add support for dashes in `Worker` names to match the Procfile process naming format. `Worker` is implicitly used when configuring HireFire using the `Configuration#dyno` method.

## v1.0.0

* Initial release.
