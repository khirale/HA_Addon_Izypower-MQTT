# Journal des modifications

## 0.2.17

- conservation locale de tous les `refresh` initiés depuis Home Assistant ;
- envoi au cloud d'un `sensor` toutes les 185 secondes à partir du même payload brut ;
- maintien du passage intégral des `refresh` initiés depuis le cloud ;
- prise en charge des topics `iot`, `Cot/izy` et `/vaysunic/vysc`.

## 0.2.13

- prise en charge des messages MQTT legacy et ENC1 ;
- publication locale normalisée sous `khirale/decoded` ;
- commandes locales génériques sous `khirale/control` ;
- corrélation locale des accusés de réception ;
- limitation des refresh initiés depuis Home Assistant ;
- distinction entre les refresh Home Assistant et cloud ;
- publication de l'état de connexion de la passerelle au VPS.
