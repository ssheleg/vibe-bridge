/* Тема: система / светлая / тёмная. Один файл на все поверхности.

   До этого тёмная тема существовала ТОЛЬКО как `prefers-color-scheme` —
   заявленный паком `data-theme` жил в одном месте проекта, в превью пака.
   То есть превью умело то, чего не умел продукт, а проверить тёмную тему
   было нельзя, не трогая настройки ОС. Из-за этого и дожил до аудита
   контраст красной строки: смотреть на неё было негде (V-10).

   Грузится СИНХРОННО из `<head>`: выбор должен примениться до первой
   отрисовки, иначе владелец видит вспышку светлой темы на каждой загрузке.
   Хранится в `localStorage`, а не в настройках моста, и это решение: тема —
   свойство УСТРОЙСТВА, а телефон и компьютер владельца вправе быть разными. */
(function () {
  var KEY = "vb-theme";

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) {
      // молчим: приватный режим запрещает хранилище. Тема тогда следует за
      // системой — это ровно то поведение, что было до переключателя.
      return null;
    }
  }

  /** Применить выбор. `system` снимает атрибут и возвращает решение ОС. */
  function apply(choice) {
    var root = document.documentElement;
    if (choice === "light" || choice === "dark") root.dataset.theme = choice;
    else delete root.dataset.theme;
  }

  function set(choice) {
    try { 
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch (e) {
      // молчим: не сохранилось — но применить всё равно надо, иначе нажатие
      // выглядит как «ничего не произошло».
    }
    apply(choice);
  }

  function current() { return stored() || "system"; }

  apply(current());
  window.vbTheme = {get: current, set: set, apply: apply};
})();
