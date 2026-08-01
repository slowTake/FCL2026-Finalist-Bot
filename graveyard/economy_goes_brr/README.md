# economy_goes_brr

A highly optimized, pure economy-focused bot designed to maximize Titanium collection and maintain a 100% win rate against the starter bot across all maps. 

`economy_goes_brr` was derived from the successful `turtleHive` codebase but stripped down to its leanest, most efficient form. It prioritizes economic expansion over everything else, strictly avoiding unnecessary expenditures that would otherwise reduce the final score.

## Key Features & Optimizations

*   **No Core Apron**: Unlike its predecessor, `economy_goes_brr` does not build a defensive or merging conveyor apron around the core. This saves between 40 and 80 Titanium in construction costs—a decisive margin on symmetric maps like `vault` where every drop of Titanium counts.
*   **No Defensive Gunners**: The bot relies entirely on out-scaling the enemy's economy. By not spending resources on defensive gunners, the bot saves hundreds of Titanium across a 1000-turn match.
*   **Deep BFS Pathfinding**: The bot utilizes an expanded BFS search limit (`BFS_MAX_ITERS = 1000`) to find paths to far-flung ore deposits, allowing builders to route long inbound relays even on complex maps.
*   **HiveMind Integration**: Uses the shared `HiveMind` state for distributed memory, map awareness, and coordinate tracking, ensuring builders don't attempt to claim the same ore sites.
*   **Committed Inbound Relays**: Builders construct a continuous inbound relay directly from the harvester back to the core. This guarantees that all harvested Titanium is successfully banked.

## Strategy Summary
The bot's strategy is simple: Zerg the map with harvesters and conveyors as quickly as possible. By avoiding the construction of structural overhead (aprons and gunners), `economy_goes_brr` builds its relays slightly faster and cheaper than the starter bot, resulting in a significantly higher final Titanium score. 

## Requirements
Uses the shared `HiveMind` and `features` components found in the `components/` directory (linked via symlinks).
