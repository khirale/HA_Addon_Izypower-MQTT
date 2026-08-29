# Khirale Izpower MQTT Gateway

Passerelle locale Khirale : elle conserve les messages MQTT bruts pour le cloud et publie une copie lisible et normalisée sous `khirale/decoded/<SN>/<famille>`.

Cette copie est produite pour les messages montants et descendants. Un payload `ENC1` est déchiffré ; un payload legacy déjà en clair est recopié tel quel. Les publications décodées ne sont pas conservées (`retain: false`).

Topics écoutés :

- `Cot/izy/#`
- `iot/#`
- `/vaysunic/vysc/sensor/#`
- `/vaysunic/vysc/refresh/#`
- `/vaysunic/vysc/cmdack`
- `/vaysunic/vysc/will`
- `/vaysunic/vysc/connect`

Exemples de sortie, indépendamment du topic d'origine :

- `khirale/decoded/<SN>/sensor`
- `khirale/decoded/<SN>/refresh`
- `khirale/decoded/<SN>/connect`
- `khirale/decoded/<SN>/will`
- `khirale/decoded/<SN>/warn`
- `khirale/decoded/<SN>/time`
- `khirale/decoded/<SN>/cmd`
- `khirale/decoded/<SN>/cmdack`
- `khirale/decoded/<SN>/ota`

Commande locale Home Assistant :

- topic : `khirale/control/<SN>/refresh`
- payload JSON clair : `{"cmd":"refresh","device":"Meter","type":"alone","sn":"<SN>","uid":123456}`

La passerelle détecte automatiquement les réponses legacy ou ENC1 afin de publier
leur copie locale en clair. Lors d'un refresh lancé depuis HA, aucun topic
`refresh` n'est envoyé au VPS. Une fois toutes les 185 secondes, le payload brut
reçu est envoyé sans aucune modification sur le topic `sensor` équivalent. Une
commande refresh descendante du cloud ouvre une fenêtre de 125 secondes durant
laquelle tous les messages `refresh` sont transmis normalement.

Chaque commande lancée depuis HA est mémorisée pendant cinq minutes à partir de
son `SN`, de son `cmd` et de son `uid`. Tout `cmdack` correspondant est conservé
uniquement en local, y compris ses éventuels doublons MQTT. Les ACK des commandes
provenant du cloud restent transmis. Ce mécanisme est générique pour les futurs
contrôles HA et n'est pas limité à `refresh`.

Pour Smart IA, la commande est toujours publiée sur
`/vaysunic/vysc/cmd/<SN>` avec un payload JSON clair. ENC1 concerne les réponses
de l'équipement, pas ses commandes. Les topics `iot/...` et `Cot/izy/...` sont les
topics de réponse de l'équipement.

État de connexion au VPS :

- topic retenu : `sites/<site>/up/status/gateway`
- `1` : passerelle connectée au broker VPS
- `0` : passerelle arrêtée ou déconnectée, publié automatiquement par le testament MQTT
