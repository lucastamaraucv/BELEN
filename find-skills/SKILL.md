---
name: find-skills
description: Ayuda a los usuarios a descubrir e instalar agent skills cuando hacen preguntas como "¿cómo hago X?", "busca una skill para X", "¿existe una skill que pueda...", o expresan interés en extender capacidades. Usa esta skill cuando el usuario busque funcionalidad que podría existir como skill instalable.
---

# Buscar Skills

Esta skill te ayuda a descubrir e instalar skills del ecosistema abierto de agent skills.

## Cuándo Usar Esta Skill

Úsala cuando el usuario:

- Pregunte "¿cómo hago X?" donde X sea una tarea común
- Diga "busca una skill para X" o "¿existe una skill para X?"
- Pregunte "¿puedes hacer X?" para capacidades especializadas
- Exprese interés en extender capacidades
- Quiera buscar herramientas, plantillas o workflows
- Mencione deseos de ayuda en dominios específicos (diseño, testing, deployment)

## ¿Qué es el Skills CLI?

El Skills CLI (`npx skills`) es el gestor de paquetes del ecosistema de agent skills abierto. Las skills son paquetes modulares que extienden capacidades con conocimiento especializado, workflows y herramientas.

**Comandos clave:**

- `npx skills find [query]` — Buscar skills interactivamente o por palabra clave
- `npx skills add <package>` — Instalar una skill desde GitHub u otras fuentes
- `npx skills check` — Verificar actualizaciones disponibles
- `npx skills update` — Actualizar todas las skills instaladas

**Explorar skills:** https://skills.sh/

## Cómo Ayudar a Usuarios a Encontrar Skills

### Paso 1: Entender la Necesidad

Identifica:

1. El dominio (React, testing, diseño, deployment, etc.)
2. La tarea específica (escribir tests, crear animaciones, revisar PRs)
3. Si es lo bastante común para que exista una skill

### Paso 2: Revisar el Ranking Primero

Antes de buscar en CLI, consulta el [ranking de skills.sh](https://skills.sh/) para skills populares. El ranking ordena por descargas totales.

Ejemplos de skills top para desarrollo web:
- `vercel-labs/agent-skills` — React, Next.js, diseño web (100K+ descargas)
- `anthropics/skills` — Diseño frontend, procesamiento de documentos (100K+)

### Paso 3: Buscar Skills

Si el ranking no cubre la necesidad, usa:

```bash
npx skills find [query]
```

Ejemplos:

- "¿cómo hago más rápida mi app React?" → `npx skills find react performance`
- "¿puedes ayudarme con revisiones de PR?" → `npx skills find pr review`
- "necesito crear un changelog" → `npx skills find changelog`

### Paso 4: Verificar Calidad Antes de Recomendar

**No recomiendes basándote solo en resultados de búsqueda.** Verifica siempre:

1. **Descargas** — Prefiere 1K+. Sé cauteloso con <100.
2. **Reputación de fuente** — Fuentes oficiales (`vercel-labs`, `anthropics`, `microsoft`) son más confiables.
3. **GitHub stars** — Repositorios con <100 stars requieren escepticismo.
4. **Seguridad** — Revisa permisos que solicita la skill. Evita skills no oficiales con permisos excesivos.

### Paso 5: Presentar Opciones

Cuando encuentres skills relevantes, presenta:

1. Nombre y descripción
2. Descargas e origen
3. Comando de instalación
4. Enlace en skills.sh

Ejemplo:

```
Encontré una skill útil: "react-best-practices" proporciona
guías de optimización de rendimiento React y Next.js de Vercel.
(185K descargas)

Para instalar:
npx skills add vercel-labs/agent-skills@react-best-practices

Más info: https://skills.sh/vercel-labs/agent-skills/react-best-practices
```

### Paso 6: Instalar

Si el usuario lo autoriza:

```bash
npx skills add <owner/repo@skill> -g -y
```

La flag `-g` instala globalmente y `-y` omite confirmaciones.

## Categorías Comunes

| Categoría           | Palabras clave                        |
| ------------------- | ------------------------------------- |
| Desarrollo Web      | react, nextjs, typescript, css        |
| Testing             | testing, jest, playwright, e2e        |
| DevOps              | deploy, docker, kubernetes, ci-cd     |
| Documentación       | docs, readme, changelog, api-docs     |
| Calidad de Código   | review, lint, refactor, best-practices|
| Diseño              | ui, ux, design-system, accessibility  |
| Productividad       | workflow, automation, git             |

## Consejos para Búsquedas Efectivas

1. **Palabras clave específicas**: "react testing" es mejor que "testing"
2. **Términos alternativos**: Si "deploy" no funciona, prueba "deployment" o "ci-cd"
3. **Fuentes populares**: Muchas skills vienen de `vercel-labs/agent-skills` o `ComposioHQ/awesome-claude-skills`

## Cuando No Hay Skills

Si no existen skills relevantes:

1. Reconoce que no se encontró
2. Ofrece ayuda directa con tus capacidades
3. Sugiere crear una skill: `npx skills init`

Ejemplo:

```
Busqué skills para "xyz" pero no encontré coincidencias.
Puedo ayudarte directamente! ¿Deseas continuar?

Si lo necesitas frecuentemente, crea tu propia skill:
npx skills init my-xyz-skill
```

## Solución de Problemas

### Skill No Encontrada

Si `npx skills find [query]` no devuelve resultados:
- Prueba términos más generales ("react" en lugar de "react-v18")
- Verifica ortografía
- Busca en https://skills.sh/ manualmente

### Permisos Insuficientes

Si la instalación falla con errores de permisos:
- Usa `npx skills add <skill> -g` para instalar a nivel usuario
- Evita usar `sudo` con comandos de npm/npx

### Conflictos de Versión

Si dos skills entran en conflicto:
- Ejecuta `npx skills check` para ver versiones instaladas
- Actualiza con `npx skills update`
- Considera desinstalar skills que no uses: `npx skills remove <skill>`

## Advertencia de Seguridad

- Instala solo de **fuentes oficiales o repositorios verificados**
- Revisa siempre los **permisos** que una skill solicita
- Para skills de terceros, verifica el **repositorio público** y comunidad antes de instalar
- Si detectas comportamiento sospechoso, reporta en https://skills.sh/report