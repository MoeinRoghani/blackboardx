# Changelog

## [0.7.0](https://github.com/MoeinRoghani/blackboardx/compare/v0.6.0...v0.7.0) (2026-08-26)


### ⚠ BREAKING CHANGES

* **api:** remove the names 0.6.0 said it removed ([#138](https://github.com/MoeinRoghani/blackboardx/issues/138))

### Features

* **api:** remove the names 0.6.0 said it removed ([#138](https://github.com/MoeinRoghani/blackboardx/issues/138)) ([914fe04](https://github.com/MoeinRoghani/blackboardx/commit/914fe0463847e951dbf47ed99950f622ec7ad7f5)), closes [#137](https://github.com/MoeinRoghani/blackboardx/issues/137)

## [0.6.0](https://github.com/MoeinRoghani/blackboardx/compare/v0.5.0...v0.6.0) (2026-08-26)


### ⚠ BREAKING CHANGES

* **model:** a creator names the agents, and one may still join later ([#132](https://github.com/MoeinRoghani/blackboardx/issues/132))

### Features

* **model:** a creator names the agents, and one may still join later ([#132](https://github.com/MoeinRoghani/blackboardx/issues/132)) ([8c1eb0b](https://github.com/MoeinRoghani/blackboardx/commit/8c1eb0b16af00e57d42122cc63a24c8db5a8ed0c)), closes [#131](https://github.com/MoeinRoghani/blackboardx/issues/131)


### Bug Fixes

* **control:** dispatch declares a return it cannot make ([#136](https://github.com/MoeinRoghani/blackboardx/issues/136)) ([f29be74](https://github.com/MoeinRoghani/blackboardx/commit/f29be74763948de09d1c0fa0d0e1774e80ded7ef)), closes [#135](https://github.com/MoeinRoghani/blackboardx/issues/135)


### Documentation

* an audit of the same failures across every page ([#128](https://github.com/MoeinRoghani/blackboardx/issues/128)) ([d4e034b](https://github.com/MoeinRoghani/blackboardx/commit/d4e034bc48c6f74ecf1594ec9441d41d2d0dec0c)), closes [#127](https://github.com/MoeinRoghani/blackboardx/issues/127)
* clauses appended to complete sentences that assert nothing ([#130](https://github.com/MoeinRoghani/blackboardx/issues/130)) ([2b715cd](https://github.com/MoeinRoghani/blackboardx/commit/2b715cd4ca88ab2efb3602c4696b349dd52c5ebf)), closes [#129](https://github.com/MoeinRoghani/blackboardx/issues/129)
* sentences that carried no claim are gone, and the rename left wreckage ([#124](https://github.com/MoeinRoghani/blackboardx/issues/124)) ([5660dc1](https://github.com/MoeinRoghani/blackboardx/commit/5660dc1a73c805fe177b53d601806de955ff6465)), closes [#123](https://github.com/MoeinRoghani/blackboardx/issues/123)
* the opening establishes the problem before naming any component ([#121](https://github.com/MoeinRoghani/blackboardx/issues/121)) ([c8b5b3b](https://github.com/MoeinRoghani/blackboardx/commit/c8b5b3ba1cbb68f4de0d422f497cea6f12729bcd)), closes [#120](https://github.com/MoeinRoghani/blackboardx/issues/120)
* the two kinds of region follow from the information, not from writing ([#126](https://github.com/MoeinRoghani/blackboardx/issues/126)) ([600a8c2](https://github.com/MoeinRoghani/blackboardx/commit/600a8c29b7938317f0080d0c3c5b3010db710ba1)), closes [#125](https://github.com/MoeinRoghani/blackboardx/issues/125)
* what a run costs when it names no agents ([#134](https://github.com/MoeinRoghani/blackboardx/issues/134)) ([ae4319e](https://github.com/MoeinRoghani/blackboardx/commit/ae4319e272566f536cb0ab384ad4a3bdd7c97e5d)), closes [#133](https://github.com/MoeinRoghani/blackboardx/issues/133)

## [0.5.0](https://github.com/MoeinRoghani/blackboardx/compare/v0.4.0...v0.5.0) (2026-08-25)


### ⚠ BREAKING CHANGES

* **board:** moving from 0.4 to 0.5 ([#119](https://github.com/MoeinRoghani/blackboardx/issues/119))
* **control:** the public names use the glossary's words ([#108](https://github.com/MoeinRoghani/blackboardx/issues/108))

### Features

* **control:** the public names use the glossary's words ([#108](https://github.com/MoeinRoghani/blackboardx/issues/108)) ([53c3338](https://github.com/MoeinRoghani/blackboardx/commit/53c3338ac57b91caf494d29f2efe5c362d7b3c79)), closes [#106](https://github.com/MoeinRoghani/blackboardx/issues/106)


### Documentation

* a glossary, and the terms that contradicted each other ([#105](https://github.com/MoeinRoghani/blackboardx/issues/105)) ([2408c7f](https://github.com/MoeinRoghani/blackboardx/commit/2408c7ffc05a1f6ad82c88f6bb14e3a921e56f73)), closes [#104](https://github.com/MoeinRoghani/blackboardx/issues/104)
* **board:** moving from 0.4 to 0.5 ([#119](https://github.com/MoeinRoghani/blackboardx/issues/119)) ([8f5c895](https://github.com/MoeinRoghani/blackboardx/commit/8f5c8955b1a79ef900f20545fe6737dbccbff04b)), closes [#118](https://github.com/MoeinRoghani/blackboardx/issues/118)
* one word for what a register holds, and it is not fact ([#110](https://github.com/MoeinRoghani/blackboardx/issues/110)) ([1525cec](https://github.com/MoeinRoghani/blackboardx/commit/1525cecb784164fa78ed30cc5e2fe04b4de0243a)), closes [#109](https://github.com/MoeinRoghani/blackboardx/issues/109)

## [0.4.0](https://github.com/MoeinRoghani/blackboardx/compare/v0.3.0...v0.4.0) (2026-08-25)


### ⚠ BREAKING CHANGES

* **board:** `Board` is renamed `InMemoryBoard`, and `board` is required by `create_model` and `Control`.

### Features

* **board:** storage is chosen, never defaulted, and SQLite is the local choice ([#89](https://github.com/MoeinRoghani/blackboardx/issues/89)) ([c6c1656](https://github.com/MoeinRoghani/blackboardx/commit/c6c1656ababb2d7ef0c079ce0e0bf8f125615a3c)), closes [#88](https://github.com/MoeinRoghani/blackboardx/issues/88)
* **storage:** a MongoDB adapter ([#93](https://github.com/MoeinRoghani/blackboardx/issues/93)) ([f81c230](https://github.com/MoeinRoghani/blackboardx/commit/f81c2307a8a439bbdf015a5b8c17be770fde75ec)), closes [#92](https://github.com/MoeinRoghani/blackboardx/issues/92)
* **storage:** a Postgres adapter, and one database holds many boards ([#91](https://github.com/MoeinRoghani/blackboardx/issues/91)) ([2de02ab](https://github.com/MoeinRoghani/blackboardx/commit/2de02abef281f3626b0177b885de55146a4696ae)), closes [#90](https://github.com/MoeinRoghani/blackboardx/issues/90)


### Bug Fixes

* **docs:** the site claims a storage adapter the package does not contain ([#85](https://github.com/MoeinRoghani/blackboardx/issues/85)) ([5bebb39](https://github.com/MoeinRoghani/blackboardx/commit/5bebb3903cca1f4ad13ab8b5f9aad92a8e0998db)), closes [#84](https://github.com/MoeinRoghani/blackboardx/issues/84)


### Documentation

* a documentation site with the structure the field uses ([#81](https://github.com/MoeinRoghani/blackboardx/issues/81)) ([4d78acc](https://github.com/MoeinRoghani/blackboardx/commit/4d78acca73f71a20c29ac4d50f005c22d88a4da2)), closes [#80](https://github.com/MoeinRoghani/blackboardx/issues/80)
* content is JSON in the record, so a service adds no serialisation ([#95](https://github.com/MoeinRoghani/blackboardx/issues/95)) ([1945a04](https://github.com/MoeinRoghani/blackboardx/commit/1945a047be510f7ea35c06b9bc15efc79a31988d)), closes [#94](https://github.com/MoeinRoghani/blackboardx/issues/94)
* **control:** the module says what the control component does now ([#101](https://github.com/MoeinRoghani/blackboardx/issues/101)) ([57d331f](https://github.com/MoeinRoghani/blackboardx/commit/57d331f8fac1e073f7f07939bdadd476e53e8b69)), closes [#100](https://github.com/MoeinRoghani/blackboardx/issues/100)
* the pages stop naming a reading limit the library does not impose ([#103](https://github.com/MoeinRoghani/blackboardx/issues/103)) ([899242e](https://github.com/MoeinRoghani/blackboardx/commit/899242e7c62a0989ee4ad0262c804b593a6e6cd4)), closes [#102](https://github.com/MoeinRoghani/blackboardx/issues/102)
* the record is durable and the run is not, and the page said otherwise ([#99](https://github.com/MoeinRoghani/blackboardx/issues/99)) ([d2797b6](https://github.com/MoeinRoghani/blackboardx/commit/d2797b6f2c67ca430ea8d32edd0f2300844bfd9e)), closes [#98](https://github.com/MoeinRoghani/blackboardx/issues/98)

## [0.3.0](https://github.com/MoeinRoghani/blackboardx/compare/v0.2.1...v0.3.0) (2026-08-24)


### ⚠ BREAKING CHANGES

* **control:** `Agent` no longer takes `acknowledgment_deadline` or `wake_cap`. `Control.extend` is removed. `Notification.deadline` is removed. `DeadlineExtended`, `PresumedFailed` and `WakeCapReached` are removed; an agent that never acknowledged is named in the outcome's `unfinished`.
* **control:** `RunBudgets` takes `wall_clock` and `idle`, and no longer takes `total_writes` or `total_notifications`. `Complete`, `FinishedWithFailures`, `BudgetExhausted`, `BudgetKind`, `BudgetReached` and `RejectionCause.BUDGET_EXHAUSTED` are removed; use `Settled`, `WallClockExpired` and `Aborted`, each carrying `unfinished`.
* **control:** `Notification.registers` is now `Notification.regions`, because it names regions of either kind.
* **model:** `create_model` no longer accepts `agents`. Register each agent with `control.register_agent` after creating the model. Registering now wakes the agent, and an agent registered during a run is woken with everything already on the board rather than only subsequent changes.

### Features

* **board:** a storage protocol so the board can be substituted ([#57](https://github.com/MoeinRoghani/blackboardx/issues/57)) ([4de8a91](https://github.com/MoeinRoghani/blackboardx/commit/4de8a911bae5b672a0c3ec38eaefa49af6729474)), closes [#55](https://github.com/MoeinRoghani/blackboardx/issues/55)
* **control:** a level write notifies the agents subscribed to that level ([#66](https://github.com/MoeinRoghani/blackboardx/issues/66)) ([3535c17](https://github.com/MoeinRoghani/blackboardx/commit/3535c17e814665f0f4c79ab51765583714012703)), closes [#64](https://github.com/MoeinRoghani/blackboardx/issues/64)
* **control:** a run closes on silence, and time is its only bound ([#69](https://github.com/MoeinRoghani/blackboardx/issues/69)) ([be3f755](https://github.com/MoeinRoghani/blackboardx/commit/be3f755f0a4d6a5c1f9394248a9ebe604c5a1c48)), closes [#67](https://github.com/MoeinRoghani/blackboardx/issues/67)
* **control:** an agent declares the regions it hears about and the levels it writes ([#63](https://github.com/MoeinRoghani/blackboardx/issues/63)) ([192936e](https://github.com/MoeinRoghani/blackboardx/commit/192936efe8acf0f45d3327dad9c05515ca7432b4)), closes [#61](https://github.com/MoeinRoghani/blackboardx/issues/61)
* **control:** the acknowledgment deadline and the wake cap are removed ([#72](https://github.com/MoeinRoghani/blackboardx/issues/72)) ([75887f9](https://github.com/MoeinRoghani/blackboardx/commit/75887f9620cab1bec9725468161acf61c32ce47f)), closes [#70](https://github.com/MoeinRoghani/blackboardx/issues/70)
* **model:** agents register themselves rather than being named at creation ([#60](https://github.com/MoeinRoghani/blackboardx/issues/60)) ([0af78e3](https://github.com/MoeinRoghani/blackboardx/commit/0af78e3f60860680ffd84164ee8e78fcc4e314bb)), closes [#58](https://github.com/MoeinRoghani/blackboardx/issues/58)


### Documentation

* the blackboard service design ([#54](https://github.com/MoeinRoghani/blackboardx/issues/54)) ([3fb20d7](https://github.com/MoeinRoghani/blackboardx/commit/3fb20d7d6a4b94ac0605fe489bb0b6369a949439)), closes [#53](https://github.com/MoeinRoghani/blackboardx/issues/53)
* the site describes the model as it now works ([#78](https://github.com/MoeinRoghani/blackboardx/issues/78)) ([893c571](https://github.com/MoeinRoghani/blackboardx/commit/893c5719011990d42a90292928b2b9c70341dec3)), closes [#76](https://github.com/MoeinRoghani/blackboardx/issues/76)

## [0.2.1](https://github.com/MoeinRoghani/blackboardx/compare/v0.2.0...v0.2.1) (2026-08-17)


### Bug Fixes

* **ci:** reach the publish workflow when a token-created release triggers nothing ([#39](https://github.com/MoeinRoghani/blackboardx/issues/39)) ([2db8f8a](https://github.com/MoeinRoghani/blackboardx/commit/2db8f8ad4b56bc2c51f16a50931b4f49df856f55)), closes [#38](https://github.com/MoeinRoghani/blackboardx/issues/38)
* **docs:** keep the decision records off the published site ([#51](https://github.com/MoeinRoghani/blackboardx/issues/51)) ([d8de96c](https://github.com/MoeinRoghani/blackboardx/commit/d8de96cbacf3f008f8fa376ef1bfcc7afb02ef6f)), closes [#50](https://github.com/MoeinRoghani/blackboardx/issues/50)
* **docs:** show the site navigation on the landing page ([#49](https://github.com/MoeinRoghani/blackboardx/issues/49)) ([e31ce4a](https://github.com/MoeinRoghani/blackboardx/commit/e31ce4a9484c9193c9b1fa105c722c4604ea0051)), closes [#48](https://github.com/MoeinRoghani/blackboardx/issues/48)


### Documentation

* a documentation site built from the repository ([#43](https://github.com/MoeinRoghani/blackboardx/issues/43)) ([f106b05](https://github.com/MoeinRoghani/blackboardx/commit/f106b05c4385feccdedb014b76c3548388ce6773)), closes [#42](https://github.com/MoeinRoghani/blackboardx/issues/42)
* the repository name in every reference to it ([#47](https://github.com/MoeinRoghani/blackboardx/issues/47)) ([d39a817](https://github.com/MoeinRoghani/blackboardx/commit/d39a8171a4d325b2c7497259cf2ccfd296fe3587)), closes [#46](https://github.com/MoeinRoghani/blackboardx/issues/46)

## [0.2.0](https://github.com/MoeinRoghani/blackboard/compare/v0.1.0...v0.2.0) (2026-08-17)


### Features

* **board:** add the board with levels, registers, and one total order ([#19](https://github.com/MoeinRoghani/blackboard/issues/19)) ([b7f65bf](https://github.com/MoeinRoghani/blackboard/commit/b7f65bf0989782f9e3bdfe9a1cd9ef0a3e19c5a6)), closes [#9](https://github.com/MoeinRoghani/blackboard/issues/9)
* **control:** admission, the write path, and the audit ([#26](https://github.com/MoeinRoghani/blackboard/issues/26)) ([e60733f](https://github.com/MoeinRoghani/blackboard/commit/e60733f226b02a0c25734ffa32aaa67b508f17cc)), closes [#21](https://github.com/MoeinRoghani/blackboard/issues/21)
* **control:** completion, budgets, and run outcomes ([#31](https://github.com/MoeinRoghani/blackboard/issues/31)) ([417c652](https://github.com/MoeinRoghani/blackboard/commit/417c652cfc7b843a3817edfe8dd0781d536e9587)), closes [#23](https://github.com/MoeinRoghani/blackboard/issues/23)
* **control:** registry, notification, and acknowledgment ([#27](https://github.com/MoeinRoghani/blackboard/issues/27)) ([97abf74](https://github.com/MoeinRoghani/blackboard/commit/97abf74a7877a09edfb21bef98915cb329439b0d)), closes [#22](https://github.com/MoeinRoghani/blackboard/issues/22)
* **model:** model creation from the six declarations ([#32](https://github.com/MoeinRoghani/blackboard/issues/32)) ([ab08957](https://github.com/MoeinRoghani/blackboard/commit/ab08957b683b2eec3a21cf134659648c9e19b966)), closes [#24](https://github.com/MoeinRoghani/blackboard/issues/24)


### Documentation

* absolute license link for the PyPI project page ([#35](https://github.com/MoeinRoghani/blackboard/issues/35)) ([10b96aa](https://github.com/MoeinRoghani/blackboard/commit/10b96aa3b48357141334c0edd0303d082e256451)), closes [#34](https://github.com/MoeinRoghani/blackboard/issues/34)
* ADR 0001, project scope ([#18](https://github.com/MoeinRoghani/blackboard/issues/18)) ([20446b8](https://github.com/MoeinRoghani/blackboard/commit/20446b8cfd7d30c65c74027a30af4df710c85e7b)), closes [#8](https://github.com/MoeinRoghani/blackboard/issues/8)
* CONTRIBUTING ([#16](https://github.com/MoeinRoghani/blackboard/issues/16)) ([901a1be](https://github.com/MoeinRoghani/blackboard/commit/901a1be59e0ecd6e61071c61a1cc8fb976ae43cb)), closes [#7](https://github.com/MoeinRoghani/blackboard/issues/7)
* README ([#20](https://github.com/MoeinRoghani/blackboard/issues/20)) ([7c9912a](https://github.com/MoeinRoghani/blackboard/commit/7c9912a384320f993843d92aaccebba1e10fdf8f)), closes [#6](https://github.com/MoeinRoghani/blackboard/issues/6)
* the release and publish procedure ([#37](https://github.com/MoeinRoghani/blackboard/issues/37)) ([bba2d6b](https://github.com/MoeinRoghani/blackboard/commit/bba2d6b21d8abc9da3e5eb1629f46e845aae5078)), closes [#36](https://github.com/MoeinRoghani/blackboard/issues/36)
